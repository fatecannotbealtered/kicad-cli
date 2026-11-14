"""The write flow (DCLI-050 to DCLI-066).

Every write, of every tier, goes through the same steps with an operation record kept
throughout: scope check, preview, confirmation or confirm-time review, lock and dispatch,
read-back, receipt. The adapter reports facts; the runtime compares the read-back with the
preview and derives ``ok`` and the error code from the receipt (DCLI-062).

A command describes its write with a ``WriteSpec``: how to choose its target, preview it,
apply it and verify it. ``DomainWrite`` does this for every domain command through the
adapter's ``preview``, ``apply`` and ``read_back``; the standard commands have their own.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import time
from typing import Any

from .adapter import BadProtocol, CapabilityError, Lost, Unanswered
from .args import quote
from .errors import DriverError, usage
from .output import Result
from .records import conflict_locked, expired, expiry, utc_now
from .registry import Command, Tiers
from .runtime import READ_DEADLINE_MS, Inputs, Run, Where
from .targets import covers, file_key, kind_of, normalize, path_of

STATE_STOPS_BATCH = {"E_BUSY", "E_HUMAN_REQUIRED", "E_BACKEND_UNAVAILABLE"}


class Verification:
    def __init__(self, status: str, observed: dict[str, Any] | None = None, mismatched: list[str] | None = None,
                 notes: list[str] | None = None) -> None:
        self.status = status
        self.observed = observed or {}
        self.mismatched = mismatched or []
        self.notes = notes or []
        self.call_failed = False  # the read-back itself failed, rather than a field being unreadable
        self.human: CapabilityError | None = None  # a dialog stopped the read-back (DCLI-066)


class WriteSpec:
    """One write call. Subclasses say how to target, preview, apply and verify it."""

    capability: str | None = None  # the adapter capability that sends the write, checked before the preview

    def __init__(self, run: Run, command: Command) -> None:
        self.run = run
        self.command = command
        self.call = run.call
        self.where = Where("file")
        self.bound: dict[str, Any] = {}
        self.conditions: list[str] = []
        self.selected: list[str] = []
        self.batch = False
        self.items: list[dict[str, Any]] | None = None
        self.secrets: dict[str, str] = {}  # secret inputs, never stored in clear
        self.pending_changes: list[dict[str, Any]] | None = None  # the changes of the current preview
        self.strict_facts = False  # apply must report change, delivery and persistence
        self.scope_keys: list[str] = []  # keys the scope must also cover, without locking them (DSEC-014)

    # The parts a write fills in --------------------------------------------------------

    def prepare(self) -> None:
        """Choose the target and check the states that refuse a write (DCLI-057)."""

    def preview(self) -> dict[str, Any]:
        """The adapter's preview result: action, targets, changes, can_apply, blocked_by."""
        raise NotImplementedError

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]],
               child: int | None = None) -> Verification:
        raise NotImplementedError

    def probe(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]],
              child: int | None = None) -> Verification:
        """A read-back with no side effect, for the pause of DVER-030."""
        return self.verify(operation_id, facts, changes, child)

    def result(self, facts: dict[str, Any], verification: Verification) -> Any:
        return facts.get("result", {})

    def untrusted_result(self) -> list[str]:
        return []

    def stored_request(self, child: int | None = None) -> dict[str, Any] | None:
        """The adapter request ``operation resolve`` and ``doc undo`` replay to read the target back."""
        return None

    def tolerances(self) -> dict[str, float]:
        """How far each changed field may read back from the preview, by field: the command's own."""
        return dict(self.command.tolerances)

    def settle_facts(self) -> dict[str, Any] | None:
        """What ``operation resolve`` needs, taken just before dispatch, to settle a standard
        write that changes no field the adapter reads back, such as the instances running."""
        return None

    def outputs(self) -> list[str]:
        """The files and folders the call names with its ``output`` flags."""
        return []

    def refresh(self) -> None:
        """Read the session, document and device again, for the review before dispatch."""
        self.run._sessions = None
        self.run._devices = None
        self.prepare()

    # Shared helpers ---------------------------------------------------------------------

    @property
    def halt(self) -> bool:
        return self.command.halt

    def gates(self, saving: bool = False) -> None:
        run, where = self.run, self.where
        if where.session is not None:
            run.gate_session(where.session, halt=self.halt)
            if run.tool.host_component and not self.halt:
                self._gate_plugin()
        if where.device is not None:
            run.gate_device(where.device, halt=self.halt)
        if not self.halt:
            run.gate_document(where.document, saving=saving)

    def _gate_plugin(self) -> None:
        """A host component loaded in another version than the one on disk refuses writes (DSEC-053)."""
        session = self.where.session
        assert session is not None
        status = self.run.capability("plugin_status", {})
        on_disk, loaded = status.get("version_on_disk"), session.get("plugin_loaded")
        if loaded and on_disk and loaded != on_disk:
            raise DriverError(
                "E_HUMAN_REQUIRED",
                f"Session {session['id']} has host component {loaded} loaded, but {on_disk} is installed; nothing was sent.",
                action="restart_application", resume=self.run.again(confirm=self.call.confirm, dangerous=self.call.dangerous),
                next_action="Ask the person to restart or reload that instance, then run `resume`.",
            )

    def session_ref(self) -> dict[str, Any] | None:
        s = self.where.session
        return {"id": s["id"], "generation": s["generation"]} if s else None

    def document_ref(self) -> dict[str, Any] | None:
        d = self.where.document
        return {"id": d["id"], "revision": d["revision"], "dirty": d["dirty"]} if d else None


class DomainWrite(WriteSpec):
    """A domain write, done through the adapter's preview, apply and read_back."""

    capability = "apply"

    def __init__(self, run: Run, command: Command) -> None:
        super().__init__(run, command)
        self.inputs: Inputs | None = None
        self.strict_facts = True

    def prepare(self) -> None:
        run = self.run
        self.where = run.where(self.command)
        self.inputs = run.inputs(self.command)
        self.selected, self.batch, self.items = self.inputs.selected, self.inputs.batch, self.inputs.items
        self.secrets = self.inputs.secrets or {}
        self.bound = {"inputs": self.inputs.bound, "document": (self.where.document or {}).get("id"),
                      "device": (self.where.device or {}).get("id")}
        if "artifact" in self.command.effects and any(made(p) for p in self.outputs()):
            self.conditions.append("target_exists")
        if self.command.long and self.batch:
            raise usage(f"{self.command.path} is a long job and takes one object per call.", "--ids")
        self.gates()

    def request(self, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.inputs is not None
        req: dict[str, Any] = {"command": self.command.path, "inputs": inputs if inputs is not None else self.inputs.adapter}
        if self.where.session:
            req["session"] = self.where.session["id"]
        if self.where.document:
            req["document"] = self.where.document["id"]
        if self.where.device:
            req["device"] = self.where.device["id"]
        return req

    def outputs(self) -> list[str]:
        assert self.inputs is not None
        return [self.inputs.adapter[self.run.key(f)] for f in self.command.flags
                if f.output and self.run.key(f) in self.inputs.adapter]

    def child_inputs(self, index: int) -> dict[str, Any]:
        assert self.inputs is not None
        base = {k: v for k, v in self.inputs.adapter.items() if k not in ("ids", "items")}
        if self.items is not None:
            return {**base, "items": [self.items[index]]}
        return {**base, "ids": [self.selected[index]]}

    def preview(self) -> dict[str, Any]:
        return self.run.capability("preview", self.request(), READ_DEADLINE_MS)

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        if self.where.device is not None and not self.halt:
            # Read the device state again, never from a cache, right before dispatch (DSEC-061).
            self.where.device = self.run.fresh_device(self.where.device["id"])
            self.run.gate_device(self.where.device)
        inputs = self.child_inputs(child) if child is not None else None
        return dispatch(self.run, "apply", {**self.request(inputs), "operation_id": operation_id})

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]],
               child: int | None = None) -> Verification:
        if self.command.verify_after_save and self.where.mode == "session":
            # The application shows this change correctly only after a save; doc save reads it
            # back and settles this record (DCLI-057).
            return Verification("deferred", notes=["The application shows this change correctly only after a save; "
                                                   "doc save verifies it."])
        tool_changes = [c for c in changes if c.get("origin", "tool") == "tool"]
        file_changes = [c for c in tool_changes if c.get("field") == "exists" and is_file_target(c["target"])]
        other = [c for c in tool_changes if c not in file_changes]
        parts = []
        # A call that made no file, of a command that makes one only in some calls, is verified
        # by its other changes alone (DCLI-057).
        if facts.get("files") or file_changes:
            parts.append(verify_files(facts, file_changes))
        if other:
            # A command that makes files and also changes the document verifies both (DCLI-057).
            inputs = self.child_inputs(child) if child is not None else None
            request = {**self.request(inputs), "operation_id": operation_id,
                       "targets": sorted({c["target"] for c in other}),
                       "fields": sorted({c["field"] for c in other if "field" in c})}
            parts.append(read_back(self.run, request, other, self.command.tolerances))
        if not parts:
            return Verification("incomplete", notes=["The preview held no change to read back, so nothing was verified."])
        return combine(parts)

    def result(self, facts: dict[str, Any], verification: Verification) -> Any:
        return facts.get("result", {})

    def untrusted_result(self) -> list[str]:
        return [f".result.{f}" for f in self.command.untrusted_fields] + [
            f".result.items[*].{f}" for f in self.command.untrusted_fields]

    def stored_request(self, child: int | None = None) -> dict[str, Any] | None:
        """Secrets never go into a record (DSEC-040); a replay gets the masked value."""
        request = self.request(self.child_inputs(child) if child is not None else None)
        secret = {self.run.key(f) for f in self.command.flags if f.secret}
        masked = self.run.contract["preview"]["masked_value"]
        inputs = {k: masked if k in secret else v for k, v in request["inputs"].items()}
        if isinstance(inputs.get("items"), list):
            inputs["items"] = [{k: masked if k in secret else v for k, v in item.items()} for item in inputs["items"]]
        request["inputs"] = inputs
        return request


def dispatch(run: Run, cap: str, request: dict[str, Any]) -> dict[str, Any]:
    """Call a write capability; errors keep their dispatch state for the receipt."""
    if not run.backend.has(cap, request.get("session")):
        raise DriverError("E_UNSUPPORTED", f"This tool's adapter has no {cap} capability, so it cannot do this.")
    reply = run.backend.call(cap, request, run.write_deadline_ms)
    run.take_notices(reply)
    return reply


def read_back(run: Run, request: dict[str, Any], changes: list[dict[str, Any]],
              tolerances: dict[Any, float | None]) -> Verification:
    """Read the changed fields back and compare them with the preview's ``after`` (DCLI-057).
    ``tolerances`` holds a tolerance by field, or by (target, field) when the changes were made by
    other writes, as for a save."""
    try:
        result = run.backend.call("read_back", request, run.read_deadline_ms)
    except (CapabilityError, Unanswered, Lost, BadProtocol) as exc:
        failed = Verification("incomplete", notes=[f"The read-back failed: {exc}"])
        failed.call_failed = True
        if isinstance(exc, CapabilityError) and exc.data.get("code") == "E_HUMAN_REQUIRED":
            failed.human = exc
        return failed
    observed = result.get("observed") if isinstance(result, dict) else None
    unreadable = result.get("unreadable", []) if isinstance(result, dict) else None
    if not isinstance(observed, dict) or not all(isinstance(v, dict) for v in observed.values()) \
            or not isinstance(unreadable, list):
        return Verification("incomplete", notes=["The read-back reply is not keyed by target key, so nothing was verified."])
    unreadable = set(map(str, unreadable))
    mismatched, missing = [], []
    for change in changes:
        field = change.get("field")
        values = observed.get(change["target"], {})
        if field is None:
            missing.append(f"(no field)@{change['target']}")
        elif field in unreadable or field not in values:
            missing.append(f"{field}@{change['target']}")
        elif not same(change["after"], values[field], tolerances.get((change["target"], field), tolerances.get(field))):
            mismatched.append(f"{field}@{change['target']}")
    if mismatched:
        return Verification("failed", observed, mismatched)
    if missing:
        return Verification("incomplete", observed, notes=[f"Not read back: {', '.join(missing)}."])
    return Verification("passed", observed)


def same(expected: Any, actual: Any, tolerance: float | None) -> bool:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and not isinstance(expected, bool) \
            and not isinstance(actual, bool):
        if tolerance is not None:
            return abs(expected - actual) <= tolerance + 1e-12
        return abs(expected - actual) <= 1e-9 * max(1.0, abs(expected), abs(actual))
    return expected == actual


def pause_before_read_back(run: Run, operation_id: str, command: str, facts: dict[str, Any],
                           changes: list[dict[str, Any]], check: Any) -> str | None:
    """For the read_back_mismatch row of a live behavior matrix (DVER-030): wait before the
    read-back so that a person or a script can change what was just written in the
    application, or delete the file it made. The wait ends early only on a real mismatch; a
    busy or blocked application keeps it waiting. The variable only makes the tool wait, and
    the receipt says that it waited; this wait is the one exception to the 60-second limit."""
    name = f"{run.tool.prefix}_TEST_PAUSE_BEFORE_READ_BACK"
    raw = run.env.get(name)
    if not raw:
        return None
    try:
        seconds = min(float(raw), float(run.contract["matrix"]["max_pause_seconds"]))
    except ValueError:
        return None
    path = run.state.path / run.contract["matrix"]["pause_file"]
    keys = sorted({c["target"] for c in changes if c.get("origin", "tool") == "tool"}
                  | {file_key(f["path"]) for f in facts.get("files", []) if isinstance(f, dict) and f.get("path")})
    path.write_text(json.dumps({"operation_id": operation_id, "command": command, "targets": keys,
                                "fields": sorted({c["field"] for c in changes if "field" in c}),
                                "seconds": seconds}), encoding="utf-8")
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            time.sleep(1)
            try:
                if check().status == "failed":
                    break
            except Exception:  # noqa: BLE001 - a read that fails now is tried again; the final read-back decides
                continue
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return f"The tool waited up to {seconds:g} s before the read-back, as {name} asked."


def reasons(pv: dict[str, Any]) -> str:
    """The preview's blocked_by as one clause, without the reasons' own final periods."""
    return "; ".join(str(r).rstrip(" .") for r in pv.get("blocked_by", []))


def is_file_target(key: str) -> bool:
    """A ``file:`` key that names a whole file, not an object inside it."""
    return key.startswith("file:///") and "#" not in key


def named_files(outputs: list[str], targets: list[dict[str, Any]]) -> list[str]:
    """The files a call names: those of its output flags and the file targets of its preview (DCLI-051)."""
    keys = {file_key(p) for p in outputs}
    keys |= {t["key"] for t in targets if t.get("kind") == "file" and is_file_target(str(t.get("key", "")))}
    return sorted(keys)


def file_checks(record: dict[str, Any], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The named files no change already checks, as changes that expect them to exist (DCLI-057)."""
    have = {c["target"] for c in changes if c.get("field") == "exists"}
    return [{"target": key, "field": "exists", "origin": "tool"} for key in record.get("_files", []) if key not in have]


def _mark_unread(record: dict[str, Any], read: bool) -> None:
    """A receipt whose document was not read after the write still names it as it was before;
    the next save draws nothing from it (DCLI-058)."""
    if (record.get("receipt") or {}).get("document") and not read:
        record["_document_unread"] = True
    else:
        record.pop("_document_unread", None)


def to_read_back(change: str, delivery: str, persistence: str, expected: list[dict[str, Any]]) -> bool:
    """A completed write is read back when its reply reports a lasting effect, and also when its
    preview or the files it names expect one, whatever the reply says (DCLI-057). A call and a
    long job decide it alike."""
    return (change in ("applied", "partial") or delivery == "delivered" or persistence in ("saved", "partial")
            or any(c.get("origin", "tool") == "tool" for c in expected))


def combine(parts: list[Verification]) -> Verification:
    """One verification from several: passed only when every part passed (DCLI-057)."""
    if len(parts) == 1:
        return parts[0]
    order = ["failed", "incomplete", "not_run", "deferred", "passed", "not_applicable"]
    status = min((p.status for p in parts), key=lambda s: order.index(s) if s in order else 0)
    out = Verification(status, {k: v for p in parts for k, v in p.observed.items()},
                       [m for p in parts for m in p.mismatched], [n for p in parts for n in p.notes])
    out.call_failed = any(p.call_failed for p in parts)
    return out


def slim_receipt(run: Run, receipt: dict[str, Any]) -> dict[str, Any]:
    """A receipt that fits the response: past ``receipt.listed_files`` files, the rest give way
    to their count and the command that writes them all to a file (DCLI-008, DCLI-057)."""
    limit = int(run.contract["receipt"]["listed_files"])
    observed = receipt.get("observed") or {}
    files = sorted((k for k in observed if is_file_target(k)), key=lambda k: observed[k].get("exists") is not False)
    if len(files) <= limit:
        return receipt
    kept = {k: v for k, v in observed.items() if k not in files[limit:]}
    command = run.cmd("operation get", [("--id", receipt["operation_id"]), ("--output", "<file>")])
    return {**receipt, "observed": kept, "more_files": {"count": len(files) - limit, "command": command}}


def slim_result(run: Run, result: Any) -> Any:
    """A result's file list keeps as many files as the receipt; the receipt's ``more_files`` leads to the rest."""
    limit = int(run.contract["receipt"]["listed_files"])
    if isinstance(result, dict) and isinstance(result.get("files"), list) and len(result["files"]) > limit:
        return {**result, "files": result["files"][:limit]}
    return result


def shorten_record(run: Run, item: dict[str, Any], room: int, keep: frozenset[str] = frozenset()) -> bool:
    """Shorten an operation record or a job too long to show within ``room`` characters, so that
    it still shows: its preview as a confirmation's, then its receipt, its result, what a
    resolution observed, then the longest lists in it, such as the targets of a batch, and last
    everything but what says what happened and the keys in ``keep`` (DCLI-008, DCLI-035,
    DCLI-220). True when it was shortened."""

    def fits() -> bool:
        return len(_compact(item)) <= room

    if fits():
        return False
    preview = item.get("preview")
    changes = preview.get("changes") if isinstance(preview, dict) else None
    while isinstance(changes, list) and changes and not fits():
        changes.pop()
        preview["changes_truncated"] = True
    details = item.get("details")
    if not fits() and isinstance(details, dict) and isinstance(item.get("receipt"), dict):
        details.pop("receipt", None)  # an ended job's error repeats the job's receipt
    if not fits() and isinstance(item.get("receipt"), dict):
        receipt = item["receipt"] = slim_receipt(run, item["receipt"])
        receipt.pop("notes", None)
        mismatched = set(receipt.get("mismatched", []))
        observed = receipt.get("observed")
        if not fits() and isinstance(observed, dict):
            receipt["observed"] = {k: v for k, v in observed.items() if k in mismatched or any(m.startswith(k) for m in mismatched)}
    if not fits() and "result" in item:
        item["result"] = slim_result(run, item["result"])
        if not fits():
            if "result" in keep:
                item["result"] = {}
            else:
                del item["result"]
    resolution = item.get("resolution")
    if not fits() and isinstance(resolution, dict):
        # How the outcome was settled stays; what the read-back saw is in the record.
        if "observed" in resolution:
            resolution["observed"] = {}
        if isinstance(resolution.get("evidence"), str) and len(resolution["evidence"]) > 300:
            resolution["evidence"] = resolution["evidence"][:300] + "…"
    while not fits():
        # A batch names its targets in the record, the preview and the receipt.
        longest = max(_lists(item), key=lambda v: len(_compact(v)), default=None)
        if longest is None:
            break
        del longest[max(1, len(longest) // 2):]
    while not fits():
        rest = [k for k in item if k not in _RECORD_CORE and k not in keep]
        if not rest:
            break
        del item[max(rest, key=lambda k: len(_compact(item[k])))]
    return True


# What an operation record or a job keeps however short it gets: what ran and how it ended.
_RECORD_CORE = {"operation_id", "command", "effects", "tier", "scope_id", "state", "planned_at", "dispatched_at",
                "finished_at", "batch", "output", "id", "cancel_requested", "started_at", "ok", "code", "message", "action",
                "resolution"}
# What the reply of a write must hold (receipt.schema.json, batch.schema.json).
_REPLY_KEYS = frozenset({"receipt", "result", "items", "summary"})


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fit_reply(run: Run, data: dict[str, Any], record: dict[str, Any]) -> None:
    """A call that changed something answers with what it did, however long: a reply too long is
    shortened, never refused, since a refusal reads as a failure and invites the Agent to send the
    change again (DCLI-008). The record keeps the whole result, and a notice names
    `operation get --output`, which writes it."""
    room = int(run.contract["envelope"]["limits"]["success_chars"]) * 3 // 4
    if len(_compact(data)) <= room:
        return
    result = data.get("result")
    job = result.get("job") if isinstance(result, dict) else None
    if "result" in data and "_job" not in record:
        record["result"] = json.loads(_compact(result))
        run.records.save(record)
    shorten_record(run, data, room, _REPLY_KEYS)
    if job is not None and not (isinstance(data.get("result"), dict) and "job" in data["result"]):
        # A long job's handle stays: without it the work cannot be followed (DCLI-065).
        data["result"] = {**(data["result"] if isinstance(data.get("result"), dict) else {}), "job": job}
    op = record["operation_id"]
    if "_job" in record:
        command = run.cmd("job get", [("--job", record["_job"]["id"])]) + " --output <file>"
        message = f"Shortened to fit the response; the job's handle stays. `{command}` writes the job, its result included."
    else:
        command = run.cmd("operation get", [("--id", op)]) + " --output <file>"
        items = f" `{run.cmd('operation list', [('--batch', op)])}` lists its items." if "items" in data else ""
        message = f"Shortened to fit the response. `{command}` writes the whole record.{items}"
    run.notices.append({"type": "shortened", "severity": "info", "message": message})


def reported(contract: Any, facts: dict[str, Any], missing: str, required: bool) -> tuple[list[str], list[str]]:
    """The change, delivery and persistence an adapter reported, and a note for each it got wrong.
    A value the contract does not have, or one that ``required`` facts leave out, counts as unknown,
    since the change may have been made; the note names the field, the value and the values allowed,
    so that the defect shows as the adapter's and not as the application's (DCLI-062)."""
    dims = contract["receipt"]["dimensions"]
    values, notes = [], []
    for d in ("change", "delivery", "persistence"):
        if d not in facts:
            values.append(missing)
            if required:
                notes.append(f"The adapter's reply has no {d}, which it must report; it counts as unknown. "
                             "This is a defect of the adapter (DCLI-062).")
        elif facts[d] in dims[d]:
            values.append(facts[d])
        else:
            values.append("unknown")
            notes.append(f"The adapter reported {d} {json.dumps(facts[d], ensure_ascii=False, default=repr)[:80]}, which is not one of "
                         f"{', '.join(dims[d])}; it counts as unknown. This is a defect of the adapter (DCLI-062).")
    return values, notes


def defect_notices(notes: list[str]) -> list[dict[str, Any]]:
    """The adapter's defects as notices, which stay when a long error drops the receipt's notes.
    A note quotes the adapter's value, so it is untrusted (DCLI-004)."""
    return [{"type": "adapter_defect", "severity": "warning", "message": note, "_untrusted": True} for note in notes]


def adapter_notices(run: Run, notes: list[str]) -> None:
    """Each defect once, and at most three, so that the items of a batch do not fill the response."""
    have = [n for n in run.notices if n.get("type") == "adapter_defect"]
    for notice in defect_notices(notes):
        if len(have) < 3 and all(h["message"] != notice["message"] for h in have):
            run.notices.append(notice)
            have.append(notice)


def _lists(value: Any) -> list[list[Any]]:
    """Every list inside the value that holds more than one item."""
    out: list[list[Any]] = []
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, list):
            if len(v) > 1:
                out.append(v)
            stack.extend(v)
        elif isinstance(v, dict):
            stack.extend(v.values())
    return out


def worker_argv(run: Run) -> list[str]:
    """The current call as arguments, with the targets the runtime chose, for a worker to run again."""
    call = run.call
    assert call is not None
    argv = call.command.path.split(" ")
    for flag in ("--session", "--doc", "--device"):
        value = call.raw.get(flag) or run.resolved.get(flag)
        if value and call.command.flag(flag):
            if flag == "--doc" and call.command.flag(flag).type == "path" or (flag == "--doc" and os.path.exists(str(value))):
                value = os.path.abspath(str(value))
            argv += [flag, str(value)]
    for name in call.given:
        flag = call.command.flag(name)
        if name in ("--session", "--doc", "--device") or (name == "--output" and not (flag and flag.output)):
            continue  # the targets come first; an --output for the full preview is no input
        if flag is not None and flag.type == "boolean":
            if call.values.get(name):
                argv.append(name)
        elif flag is not None and flag.type == "path":
            argv += [name, os.path.abspath(str(call.values[name]))]  # the worker may start elsewhere
        else:
            argv += [name, call.raw[name]]
    return argv


def made(path: str) -> bool:
    """A file exists, or a folder holds at least one file or folder (DCLI-051)."""
    if os.path.isdir(path):
        with os.scandir(path) as entries:
            return any(True for _ in entries)
    return os.path.isfile(path)


def verify_files(facts: dict[str, Any], changes: list[dict[str, Any]]) -> Verification:
    """Every produced file exists; its size and SHA-256 are recorded. A folder an ``output`` flag
    names counts as made when it holds something; the files in it are those in ``files``. A file
    listed with ``requested_path`` counts for that path: the application wrote it under another
    name, such as a numbered version (DCLI-057)."""
    paths = [f["path"] for f in facts.get("files", [])]
    stand_ins = {normalize(file_key(f["requested_path"])) for f in facts.get("files", []) if isinstance(f.get("requested_path"), str)}
    for change in changes:
        path = path_of(change["target"])
        if path and change.get("field") == "exists" and path not in paths and normalize(file_key(path)) not in stand_ins:
            paths.append(path)
    if not paths:
        return Verification("incomplete", notes=["The write named no file to check."])
    observed, missing = {}, []
    for path in paths:
        key = file_key(path)
        if os.path.isdir(path) and made(path):
            observed[key] = {"exists": True, "path": path, "folder": True}
            continue
        if not os.path.isfile(path):
            observed[key] = {"exists": False}
            missing.append(f"exists@{key}")
            continue
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        observed[key] = {"exists": True, "path": path, "bytes": os.path.getsize(path), "sha256": digest.hexdigest()}
    return Verification("failed" if missing else "passed", observed, missing)


class Engine:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.contract = run.contract
        self.tiers = Tiers(run.contract)

    # ------------------------------------------------------------------ the flow

    def execute(self, spec: WriteSpec) -> Result:
        run, call, command = self.run, self.run.call, spec.command
        assert call is not None
        run.records.check_writable()
        run.records.purge()
        # A confirming call looks its token up first (DCLI-056, step 1).
        token_record = self.lookup_token() if call.confirm else None
        spec.prepare()
        session = spec.where.session["id"] if spec.where.session else None
        if spec.capability and not run.backend.has(spec.capability, session):
            # Neither a preview nor a token promises a write that cannot be sent (DCLI-260).
            where = f"the instance of session {session}" if session else "this tool's adapter"
            raise DriverError("E_UNSUPPORTED", f"{where[0].upper()}{where[1:]} has no {spec.capability} capability, "
                                               f"so {command.path} cannot run there; nothing was sent.")
        pv = spec.preview()
        self.take_raises(spec, pv)
        targets = self._targets(spec, pv)
        changes = self._changes(pv)
        keys = _keys(targets, changes) or self.named_keys(spec)
        tier = self.tier(spec, targets, changes)
        approved = (token_record or {}).get("preview", {}).get("tier")
        if approved and self.contract.tier_rank(tier) > self.contract.tier_rank(approved):
            # The call became stricter than the person confirmed (DCLI-052, DCLI-056 step 8).
            raise conflict("preview_changed", f"The call is now {tier}, not the {approved} that was confirmed; nothing was sent.",
                           "Run the command again without --confirm to see the new preview.")
        dangerous = tier == "dangerous"
        if command.needs_scope:
            self.check_scope(command, dangerous, keys + spec.scope_keys, spec.halt)
        if not spec.halt and not call.confirm:
            # A locked target gets no token: the write could not run (DCLI-060).
            busy = run.locks.holders(keys)
            if busy and not call.dry_run:
                raise conflict_locked(busy[0], run.tool.name)
            if busy:
                pv = {**pv, "can_apply": False,
                      "blocked_by": list(pv.get("blocked_by", [])) + [f"The target is locked by operation {busy[0]['operation_id']}."]}
        if not pv.get("can_apply", True) and not call.dry_run and not call.confirm:
            raise DriverError("E_CONFLICT", f"The change cannot be applied: {reasons(pv)}.",
                              conflict="blocked", untrusted=["message"], **self.blocked_help(pv, "Resolve what blocks it, then run the command again."))
        spec.pending_changes = changes
        binds = self.binds(spec, keys)
        if token_record is not None:
            record = self.review_token(spec, token_record, binds, keys, pv)
            operation_id = record["operation_id"]
        else:
            operation_id = run.records.new_id()
            preview = self.build_preview(operation_id, spec, pv, targets, changes, tier)
            confirm = tier in ("two_step", "dangerous")
            record = self.new_record(operation_id, spec, targets, preview, tier)
            if confirm:
                token = secrets.token_urlsafe(self.contract["confirm_token"]["random_bits"] // 8)
                record["_token"] = {"value": token, "binds": binds, "expires_at": expiry(self.contract["confirm_token"]["ttl_seconds"]),
                                    "spent": False}
            if call.dry_run or confirm:
                run.records.save(record)
                self.write_full_preview(spec, preview, changes)
                return self.answer_preview(record, preview, spec, dry_run=call.dry_run)
            self.review_direct(spec, binds, keys, tier)
            run.records.save(record)
        return self.dispatch(spec, record, targets, changes, keys)

    # ------------------------------------------------------------------ tiers and scope

    def tier(self, spec: WriteSpec, targets: list[dict[str, Any]], changes: list[dict[str, Any]]) -> str:
        """The tier of this call (DCLI-051, DCLI-052, DSEC-013, DSEC-018)."""
        command = spec.command
        tier = self.tiers.base(command, spec.where.mode)
        if command.halt:
            return "direct"
        raises = {t["when"]: t["tier"] for t in command.tier_when}
        for condition in spec.conditions:
            if condition in raises:
                tier = self.tiers.strictest([tier, raises[condition]])
        if tier == "direct" and command.kind == "domain" and self.touches_more(spec, targets, changes):
            tier = "two_step"  # the preview touches more than the inputs name (DCLI-052)
        if self.run.scope.confirm_always and tier == "direct":
            tier = "two_step"
        return tier

    @staticmethod
    def take_raises(spec: WriteSpec, pv: dict[str, Any]) -> None:
        """The conditions the adapter's preview found that make this call stricter: only
        those the command registered in tier_when (DCLI-031, DCLI-052)."""
        raises = pv.get("tier_raises") or []
        registered = {t["when"] for t in spec.command.tier_when}
        unknown = [w for w in raises if w not in registered]
        if unknown:
            raise DriverError("E_PROTOCOL", f"The adapter's preview raises the tier for {unknown[0]!r}, which the command does not register.")
        spec.conditions.extend(w for w in raises if w not in spec.conditions)

    def touches_more(self, spec: WriteSpec, targets: list[dict[str, Any]], changes: list[dict[str, Any]]) -> bool:
        """Whether a preview reaches past the objects the inputs name: more targets than
        selected objects, or a change outside them. Without selected objects the inputs name
        the document or the device."""
        outputs = [file_key(p) for p in spec.outputs()]
        if spec.selected:
            if len(targets) > len(spec.selected) + len(outputs):
                return True
            named = [t["key"] for t in targets[:len(spec.selected)]]
        elif spec.where.document is not None:
            named = [self.run.doc_key(spec.where.document)]
        elif spec.where.device is not None:
            named = [f"device:{spec.where.device['id']}"]
        else:
            named = []
        named += outputs  # the files an artifact command creates are named by its inputs
        keys = [t["key"] for t in targets] + [c["target"] for c in changes]
        return any(not any(covers(n, k) for n in named) for k in keys)

    def check_scope(self, command: Command, dangerous: bool, keys: list[str], halt: bool) -> None:
        scope = self.run.scope
        missing = scope.missing(command.effects, dangerous, keys, halt=halt)
        if missing is None:
            return
        status = scope.status
        why = {"missing": f"there is no scope file at {scope.path}", "invalid": f"the scope file {scope.path} is invalid ({scope.problem})",
               "expired": f"the scope {scope.scope_id} has expired", "granted": f"the scope {scope.scope_id} does not cover it"}[status]
        raise DriverError(
            "E_FORBIDDEN", f"This write is not authorized: {why}. Nothing was sent.", scope=missing,
        )

    # ------------------------------------------------------------------ preview

    def named_keys(self, spec: WriteSpec) -> list[str]:
        """The keys a call names itself, for a preview that lists no target (DSEC-014, DCLI-060)."""
        if spec.where.document is not None:
            return [self.run.doc_key(spec.where.document)]
        if spec.where.device is not None:
            return [f"device:{spec.where.device['id']}"]
        return []

    @staticmethod
    def _targets(spec: WriteSpec, pv: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for target in pv.get("targets", []):
            entry = {"key": target["key"], "kind": target.get("kind") or spec.command.target_kind}
            if target.get("label"):
                entry["label"] = target["label"]
            out.append(entry)
        if spec.batch and len(out) != len(spec.selected):
            raise DriverError("E_PROTOCOL", "The adapter's preview of a batch must list one target per selected object, in order.")
        if spec.selected and len(out) < len(spec.selected):
            raise DriverError("E_PROTOCOL", "The adapter's preview lists no target for the selected object; nothing was sent.")
        return out

    @staticmethod
    def _changes(pv: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for change in pv.get("changes", []):
            out.append({**change, "origin": change.get("origin", "tool")})
        for change in pv.get("external_changes", []):
            out.append({**change, "origin": "external"})
        return out

    def build_preview(self, operation_id: str, spec: WriteSpec, pv: dict[str, Any], targets: list[dict[str, Any]],
                      changes: list[dict[str, Any]], tier: str) -> dict[str, Any]:
        self._all_changes = changes
        command = spec.command
        limit = self.contract["preview"]["max_changes"]
        by_origin: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for change in changes:
            by_origin[change["origin"]] = by_origin.get(change["origin"], 0) + 1
            kind = next((t["kind"] for t in targets if covers(t["key"], change["target"])), kind_of(change["target"]))
            by_kind[kind] = by_kind.get(kind, 0) + 1
        preview: dict[str, Any] = {"operation_id": operation_id, "command": command.path,
                                   "action": pv.get("action") or command.summary}
        if targets:
            preview["target"] = targets if spec.batch or len(targets) > 1 else targets[0]
        preview.update(effects=list(command.effects), tier=tier,
                       summary={"total": len(changes), "by_origin": by_origin, "by_kind": by_kind},
                       changes=self.mask(spec, copy.deepcopy(changes[:limit])))
        if len(changes) > limit:
            preview["changes_truncated"] = True
        if spec.session_ref():
            preview["session"] = spec.session_ref()
        if spec.document_ref():
            preview["document"] = spec.document_ref()
        preview["can_apply"] = bool(pv.get("can_apply", True))
        if not preview["can_apply"]:
            preview["blocked_by"] = list(pv.get("blocked_by", [])) or ["the application gave no reason"]
        return preview

    def secrets(self, spec: WriteSpec) -> set[str]:
        return {self.run.key(f) for f in spec.command.flags if f.secret}

    def mask(self, spec: WriteSpec, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A secret never shows in a preview or a record, even as a change (DSEC-040)."""
        secret, masked = self.secrets(spec), self.contract["preview"]["masked_value"]
        for change in changes:
            if change.get("field") in secret:
                change.update(before=masked, after=masked)
        return changes

    def mask_observed(self, spec: WriteSpec, observed: dict[str, Any]) -> dict[str, Any]:
        secret, masked = self.secrets(spec), self.contract["preview"]["masked_value"]
        return {k: ({f: masked if f in secret else v for f, v in values.items()} if isinstance(values, dict) else values)
                for k, values in observed.items()}

    def write_full_preview(self, spec: WriteSpec, preview: dict[str, Any], changes: list[dict[str, Any]]) -> None:
        """--output on a write gives the full preview, with every change, in a file (DCLI-054)."""
        flag = spec.command.flag("--output")
        if flag is None or flag.output or not spec.call.has("--output"):
            return
        full = {**preview, "changes": self.mask(spec, copy.deepcopy(changes))}
        full.pop("changes_truncated", None)
        try:
            with open(spec.call.values["--output"], "w", encoding="utf-8") as handle:
                json.dump(full, handle, ensure_ascii=False, indent=1)
        except OSError as exc:
            raise DriverError("E_IO", f"Cannot write the preview to {spec.call.values['--output']}: {exc.strerror}.") from None

    @staticmethod
    def preview_untrusted(prefix: str, preview: dict[str, Any]) -> list[str]:
        paths = [f"{prefix}.action", f"{prefix}.changes[*].before", f"{prefix}.changes[*].after", f"{prefix}.blocked_by"]
        target = preview.get("target")
        paths.append(f"{prefix}.target[*].label" if isinstance(target, list) else f"{prefix}.target.label")
        return paths

    def answer_preview(self, record: dict[str, Any], preview: dict[str, Any], spec: WriteSpec, dry_run: bool) -> Result:
        token = record.get("_token")
        if dry_run:
            data: dict[str, Any] = {"preview": preview}
            if token:
                data.update(confirm_token=token["value"], expires_at=token["expires_at"])
            return Result(data, self.preview_untrusted(".preview", preview))
        raise self.confirmation_required(record, spec)

    def confirmation_required(self, record: dict[str, Any], spec: WriteSpec) -> DriverError:
        token, preview = record["_token"], record["preview"]
        dangerous = preview["tier"] == "dangerous"
        line = self.run.again(confirm=token["value"], dangerous=dangerous, drop=("--dry-run",))
        flags = "`--confirm <token>` and `--dangerous`" if dangerous else "`--confirm <token>`"
        return DriverError(
            "E_CONFIRMATION_REQUIRED",
            f"Nothing changed. Check the preview, then run the same command again with {flags}.",
            preview=preview, confirm_token=token["value"], expires_at=token["expires_at"],
            next_action=f"Check the preview against the request, then run `{line}`.",
            untrusted=[p[1:] for p in self.preview_untrusted(".details.preview", preview)] + ["details.next_action"],
        )

    # ------------------------------------------------------------------ records

    def new_record(self, operation_id: str, spec: WriteSpec, targets: list[dict[str, Any]], preview: dict[str, Any],
                   tier: str) -> dict[str, Any]:
        record: dict[str, Any] = {"operation_id": operation_id, "command": spec.command.path}
        if targets:
            record["target"] = targets if spec.batch or len(targets) > 1 else targets[0]
        record.update(effects=list(spec.command.effects), tier=tier, scope_id=self.run.scope.scope_id or "",
                      state="planned", preview=preview, planned_at=utc_now())
        record["_seq"] = time.time_ns()  # newest first, also within one second (DCLI-035)
        record["_call"] = {"session": (spec.where.session or {}).get("id"), "document": (spec.where.document or {}).get("id"),
                           "path": (spec.where.document or {}).get("path"),
                           "device": (spec.where.device or {}).get("id"), "mode": spec.where.mode}
        request = spec.stored_request()
        if request is not None:
            record["_request"] = request
            record["_tolerances"] = spec.tolerances()
        if spec.command.long and spec.command.kind == "standard":
            record["_argv"] = worker_argv(self.run)  # its worker runs the same call (DCLI-065)
        record["_changes"] = self.mask(spec, [copy.deepcopy(c) for c in self._all_changes
                                              if c.get("origin", "tool") == "tool"])
        files = named_files(spec.outputs(), targets)
        if files:
            record["_files"] = files
        return record

    def binds(self, spec: WriteSpec, keys: list[str]) -> dict[str, Any]:
        """The values a confirm token binds (DCLI-055)."""
        session, doc, scope = spec.where.session, spec.where.document, self.run.scope
        return {
            "command": spec.command.path, "inputs": spec.bound, "target": sorted(keys),
            "session_id": session["id"] if session else None,
            "session_generation": session["generation"] if session else None,
            "document_revision": doc["revision"] if doc else None, "document_dirty": doc["dirty"] if doc else None,
            "plugin_version": session.get("plugin_loaded") if session else None,
            "scope_id": scope.scope_id or None, "scope_hash": scope.hash,
            "secrets": _secret_digests(spec.secrets, secrets.token_hex(16)),
            "changes": changes_digest(spec.pending_changes),
        }

    # ------------------------------------------------------------------ confirm-time review

    def lookup_token(self) -> dict[str, Any]:
        """Step 1 of DCLI-056: the token is valid, not used and not expired."""
        run, call = self.run, self.run.call
        assert call is not None and call.confirm is not None
        record = run.records.by_token(call.confirm)
        if record is None:
            raise conflict("token_invalid", "The confirm token is not valid; nothing was sent.",
                           "Run the command again without --confirm to get a new preview and token.")
        token = record["_token"]
        if token.get("spent") or record.get("state") != "planned":
            raise conflict("token_used", "The confirm token has already been used; nothing was sent.",
                           f"Check the result with `{run.tool.name} operation get --id {record['operation_id']}` before anything else.")
        if expired(token["expires_at"]):
            raise conflict("token_expired", "The confirm token has expired; nothing was sent.",
                           "Run the command again without --confirm to get a new preview and token.")
        return record

    def review_token(self, spec: WriteSpec, record: dict[str, Any], binds: dict[str, Any], keys: list[str],
                     pv: dict[str, Any]) -> dict[str, Any]:
        """Steps 2 to 8 of DCLI-056, stopping at the first failure."""
        call = self.run.call
        assert call is not None
        old = record["_token"]["binds"]
        if old["command"] != binds["command"]:
            raise conflict("token_mismatch", f"The token was issued for {old['command']}, not {binds['command']}; nothing was sent.",
                           "Run the command again without --confirm to get a new preview and token.")
        differs = _differs(old["inputs"], binds["inputs"], spec.command)
        stored = old.get("secrets") or {"salt": "", "digests": {}}
        if differs is None and _secret_digests(spec.secrets, stored["salt"])["digests"] != stored["digests"]:
            differs = next((_flag_for(k, spec.command) for k in sorted(set(spec.secrets) | set(stored["digests"]))
                            if _secret_digests({k: spec.secrets.get(k, "")}, stored["salt"])["digests"].get(k)
                            != stored["digests"].get(k)), "--input")
        if differs:
            raise conflict("token_mismatch", f"{differs} differs from the previewed call; nothing was sent.",
                           "Confirm with exactly the previewed arguments, or run the command again without --confirm.",
                           parameter=differs)
        if record["preview"]["tier"] == "dangerous" and not call.dangerous:
            raise self.confirmation_required(record, spec)
        self.review_state(spec, old, keys, pv, record["operation_id"], record["preview"]["tier"])
        return record

    def review_direct(self, spec: WriteSpec, binds: dict[str, Any], keys: list[str], tier: str) -> None:
        """Steps 3 to 8 of DCLI-056 for a direct-tier write, against the state of its preview."""
        spec.refresh()
        pv = spec.preview()
        self.run.__dict__.pop("scope", None)  # read the scope file again
        self.review_state(spec, binds, keys, pv, None, tier)

    def review_state(self, spec: WriteSpec, old: dict[str, Any], keys: list[str], pv: dict[str, Any],
                     operation_id: str | None, tier: str) -> None:
        run = self.run
        scope = run.scope
        if spec.command.needs_scope:
            self.check_scope(spec.command, tier == "dangerous", keys + spec.scope_keys, spec.halt)
            if scope.scope_id != (old["scope_id"] or "") or scope.hash != old["scope_hash"]:
                raise conflict("scope_changed", "The scope file changed after the preview; nothing was sent.",
                               "Run the command again without --confirm to preview it under the new scope.")
        if not spec.halt:
            busy = run.locks.holders(keys, exclude=operation_id)
            if busy:
                raise conflict_locked(busy[0], run.tool.name)
        session, doc = spec.where.session, spec.where.document
        if old["session_id"] is not None:
            if session is None or session["id"] != old["session_id"] or session["generation"] != old["session_generation"]:
                raise conflict("session_changed", "The instance reconnected or restarted after the preview; nothing was sent.",
                               "Run the command again without --confirm to preview it again.")
        if old["document_revision"] is not None:
            if doc is None or doc["revision"] != old["document_revision"] or doc["dirty"] != old["document_dirty"]:
                raise conflict("document_changed", "The document changed after the preview; nothing was sent.",
                               "Read the document again, then run the command again without --confirm.")
        if old["plugin_version"] is not None and session is not None and session.get("plugin_loaded") != old["plugin_version"]:
            raise conflict("plugin_changed", "The host component changed after the preview; nothing was sent.",
                           "Run the command again without --confirm to preview it again.")
        if old.get("changes") is not None and changes_digest(self._changes(pv)) != old["changes"]:
            raise conflict("preview_changed", "The change would now differ from the previewed one; nothing was sent.",
                           "Run the command again without --confirm to see the new preview.")
        if operation_id is not None and sorted(keys) != old["target"]:
            raise conflict("token_mismatch", "The call now reaches other targets than the previewed one; nothing was sent.",
                           "Run the command again without --confirm to preview it again.")
        if not pv.get("can_apply", True):
            help_ = self.blocked_help(pv, "Resolve what blocks it, then run the command again without --confirm.")
            raise conflict("blocked", f"The change can no longer be applied: {reasons(pv)}.",
                           help_.pop("next_action"), untrusted=["message"], **help_)

    def blocked_help(self, pv: dict[str, Any], then: str) -> dict[str, Any]:
        """The next action for a blocked preview, through the knowledge entries the adapter named (DSKL-124)."""
        ids = self.run.knowledge_ids(pv)
        if ids:
            return {"knowledge": ids, "next_action": self.run.knowledge_first(ids, then)}
        return {"next_action": then}

    # ------------------------------------------------------------------ dispatch

    def dispatch(self, spec: WriteSpec, record: dict[str, Any], targets: list[dict[str, Any]],
                 changes: list[dict[str, Any]], keys: list[str]) -> Result:
        run = self.run
        operation_id = record["operation_id"]
        settle = spec.settle_facts()
        with run.mutex.hold():
            # The token is spent and the targets locked in one step, so two confirms of one
            # token cannot both dispatch (DCLI-055, DCLI-060).
            fresh = run.records.load(operation_id)
            if fresh is None:
                raise DriverError("E_IO", f"The record of {operation_id} cannot be read; nothing was sent.")
            if fresh.get("_token", {}).get("spent") or fresh.get("state") != "planned":
                raise conflict("token_used", "The confirm token has already been used; nothing was sent.",
                               f"Check the result with `{run.tool.name} operation get --id {operation_id}` before anything else.")
            if not spec.halt:  # a halt command is never blocked by the lock of the work it stops (DCLI-060)
                run.locks.acquire_held(operation_id, keys)
            record = fresh
            if "_token" in record:
                record["_token"]["spent"] = True
            record.update(state="dispatched", dispatched_at=utc_now(), _keys=sorted(keys), _batch=spec.batch)
            if settle is not None:
                record["_settle"] = settle
            try:
                run.records.save(record)
            except DriverError:
                run.locks._remove(operation_id)
                raise
        run.dispatching = operation_id
        if spec.batch:
            return self.run_batch(spec, record, targets, changes)
        outcome = self.one(spec, record, targets[0] if targets else None, changes, None)
        return self.finish_single(spec, record, outcome)

    def one(self, spec: WriteSpec, record: dict[str, Any], target: dict[str, Any] | None,
            changes: list[dict[str, Any]], child: int | None, on_hung: Any = None) -> dict[str, Any]:
        """Apply one write and build its receipt. Returns the outcome for the caller to report.
        ``on_hung``: for an item of a batch, ends the batch when its read-back hangs."""
        operation_id = record["operation_id"]
        receipt: dict[str, Any] = {"operation_id": operation_id, "command": spec.command.path}
        if target is not None:
            receipt["target"] = target
        receipt.update(effects=list(spec.command.effects), tier=record["preview"]["tier"] if "preview" in record else record["tier"])
        out: dict[str, Any] = {"receipt": receipt, "facts": {}, "error": None, "job": None, "unspend": False, "stop": False}
        if spec.command.long:
            # The runtime runs the work in its own worker; this call only starts it (DCLI-065).
            from .jobs import new_job_id

            job = {"id": new_job_id(), "state": "running", "worker": True}
            out.update(job=job, facts={"dispatch_state": "dispatched", "job": job})
            receipt["outcome"] = _outcome("completed", "not_applicable", "not_applicable", "not_applicable", "not_applicable")
            receipt.update(observed={}, mismatched=[], notes=[f"The work continues as long job {job['id']}."])
            return out
        try:
            facts = spec.apply(operation_id, child)
        except DriverError as exc:  # refused before dispatch, such as a device in fault
            out.update(error=exc, unspend=True, stop=exc.code in STATE_STOPS_BATCH)
            receipt["outcome"] = _outcome("not_started", "not_applied")
            receipt.update(observed={}, mismatched=[])
            return out
        except CapabilityError as exc:
            return self._failed(spec, out, exc)
        except Lost as exc:
            out.update(error=DriverError("E_BACKEND_UNAVAILABLE", f"{exc}; the session is lost. Nothing was sent."),
                       unspend=True, stop=True)
            receipt["outcome"] = _outcome("not_started", "not_applied")
            receipt.update(observed={}, mismatched=[])
            return out
        except BadProtocol as exc:
            out.update(error=DriverError("E_PROTOCOL", f"The host component cannot be used: {exc}. Nothing was sent."),
                       unspend=True, stop=True)
            receipt["outcome"] = _outcome("not_started", "not_applied")
            receipt.update(observed={}, mismatched=[])
            return out
        except Unanswered as exc:
            if not exc.sent:
                out.update(error=DriverError("E_BUSY", f"The instance did not answer before the write was sent: {exc}. Nothing was sent.",
                                             retry_after_ms=2000), unspend=True, stop=True)
                receipt["outcome"] = _outcome("not_started", "not_applied")
                receipt.update(observed={}, mismatched=[])
                return out
            receipt["outcome"] = _outcome("unknown", "unknown", "unknown", "unknown", "not_run")
            receipt.update(observed={}, mismatched=[], notes=[f"No reply after dispatch: {exc}"])
            out["stop"] = True
            return out
        if not isinstance(facts, dict) or facts.get("dispatch_state") not in ("not_started", "dispatched", "unknown"):
            receipt["outcome"] = _outcome("unknown", "unknown", "unknown", "unknown", "not_run")
            receipt.update(observed={}, mismatched=[], notes=["The adapter's reply has no valid dispatch_state."])
            out["stop"] = True
            return out
        out["facts"] = facts
        if facts.get("job"):
            job = facts["job"]
            out["job"] = job
            (_, delivery, persistence), wrong = reported(self.contract, {k: v for k, v in facts.items() if k != "change"},
                                                         "not_applicable", False)
            adapter_notices(self.run, wrong)
            receipt["outcome"] = _outcome("completed", "not_applicable", delivery, persistence, "not_applicable")
            receipt.update(observed={}, mismatched=[], notes=[f"The work continues as long job {job['id']}."] + wrong)
            return out
        ds = facts["dispatch_state"]
        execution = {"unknown": "unknown", "not_started": "not_started"}.get(ds, "completed")
        # A dimension named wrongly is not known (DCLI-062). Only apply must report all three;
        # the other write capabilities report what applies to them (adapter.schema.json).
        missing = "unknown" if spec.strict_facts else "not_applicable"
        (change, delivery, persistence), wrong = reported(self.contract, facts, missing, spec.strict_facts)
        adapter_notices(self.run, wrong)
        verification = Verification("not_applicable")
        paused = None
        if execution == "completed" and to_read_back(change, delivery, persistence, changes + file_checks(record, changes)):
            if child is None or on_hung is not None:
                def settle(note: str) -> Result:
                    # apply replied, so a read after it that hangs leaves the outcome known (DCLI-278).
                    receipt["outcome"] = _outcome(execution, change, delivery, persistence, "incomplete")
                    receipt.update(observed={}, mismatched=[], notes=[note])
                    return self.finish_single(spec, record, out, hung=True) if child is None else on_hung(out)

                self.run.settle_hang = settle
            try:
                paused = self.pause_before_read_back(spec, operation_id, facts, changes, child)
                verification = spec.verify(operation_id, facts, changes + file_checks(record, changes), child)
            except Exception as exc:  # noqa: BLE001 - after dispatch, a failure to verify is not a known outcome
                verification = Verification("incomplete", notes=[f"The read-back could not be done: {type(exc).__name__}: {exc}"])
            finally:
                self.run.settle_hang = None
            if paused is not None:
                verification.notes.insert(0, paused)
        receipt["outcome"] = _outcome(execution, change, delivery, persistence, verification.status)
        receipt.update(observed=self.mask_observed(spec, verification.observed), mismatched=verification.mismatched)
        if verification.notes or wrong:
            receipt["notes"] = wrong + verification.notes
        if isinstance(facts.get("native_message"), str):
            receipt.setdefault("notes", []).append(f"The application said: {facts['native_message']}")
        return out

    def pause_before_read_back(self, spec: WriteSpec, operation_id: str, facts: dict[str, Any],
                               changes: list[dict[str, Any]], child: int | None) -> str | None:
        if spec.command.verify_after_save and spec.where.mode == "session":
            return None  # its read-back happens at the save, where the pause applies (DVER-030)
        return pause_before_read_back(self.run, operation_id, spec.command.path, facts, changes,
                                      lambda: spec.probe(operation_id, facts, changes, child))

    def _failed(self, spec: WriteSpec, out: dict[str, Any], exc: CapabilityError) -> dict[str, Any]:
        receipt = out["receipt"]
        code, ds = exc.code, exc.dispatch_state
        receipt.update(observed={}, mismatched=[])
        error = self.run.host_error(exc, ds, write=True)
        if ds == "not_started":
            receipt["outcome"] = _outcome("not_started", "not_applied")
            out.update(error=error, unspend=code in ("E_BUSY", "E_HUMAN_REQUIRED"), stop=code in STATE_STOPS_BATCH)
        elif ds == "dispatched" and code == "E_NATIVE_FAILURE":
            receipt["outcome"] = _outcome("failed", "rolled_back")
            out["error"] = error
        elif ds == "dispatched" and code in ("E_VALIDATION", "E_NOT_FOUND", "E_UNSUPPORTED"):
            receipt["outcome"] = _outcome("failed", "not_applied")
            out["error"] = error
        else:
            # A dialog the change itself opened, or any other error after dispatch: the
            # change may have taken effect (DCLI-063, DCLI-066).
            receipt["outcome"] = _outcome("unknown", "unknown", "unknown", "unknown", "not_run")
            receipt["notes"] = [f"The application reported {code} after the change was sent."]
            out.update(human=error if code == "E_HUMAN_REQUIRED" else None, stop=True)
        if isinstance(exc.data.get("native_message"), str):
            receipt.setdefault("notes", []).append(f"The application said: {exc.data['native_message']}")
        return out

    def derive(self, receipt: dict[str, Any], cause: str | None) -> tuple[bool, str | None]:
        """``ok`` and the error code from the receipt, by the first matching rule (DCLI-062)."""
        outcome = receipt["outcome"]

        def holds(cond: dict[str, list[str]]) -> bool:
            return all(outcome[dim] in values for dim, values in cond.items())

        for rule in self.contract["derivation"]:
            if rule.get("otherwise"):
                return True, None
            if "all" in rule and not all(holds(c) for c in rule["all"]):
                continue
            if "any" in rule and not any(holds(c) for c in rule["any"]):
                continue
            code = rule["code"]
            if code == "cause":
                code = cause or rule["default_code"]
            return False, code
        return True, None

    # ------------------------------------------------------------------ one write

    def finish_single(self, spec: WriteSpec, record: dict[str, Any], out: dict[str, Any], hung: bool = False) -> Result:
        """``hung``: an adapter call still hangs on the main thread, so nothing may call the adapter."""
        run = self.run
        operation_id = record["operation_id"]
        receipt = out["receipt"]
        if not hung:
            # The outcome is known; a read that hangs from here on leaves it as it is (DCLI-278).
            run.settle_hang = lambda note: self.finish_single(spec, record, out, hung=True)
        try:
            read = self._decorate(spec, receipt, fresh=not hung)
        finally:
            if not hung:
                run.settle_hang = None
        error: DriverError | None = out["error"]
        if out["unspend"]:
            # Refused before anything was sent: a token stays usable (DCLI-055).
            assert error is not None
            if "_token" in record:
                record["state"] = "planned"
                record.pop("dispatched_at", None)
                record["_token"]["spent"] = False
            else:
                record.update(state="failed", receipt=receipt, finished_at=utc_now())
            with run.mutex.hold():  # the token comes back and the lock goes in one step
                run.records.save(record)
                run.locks._remove(operation_id)
            run.dispatching = None
            raise error
        record["receipt"] = receipt
        _mark_unread(record, read)
        if out["job"]:
            job = out["job"]
            # ``again`` is the write's first call, for a job that ends having changed nothing (DCLI-220).
            record["_job"] = {"id": job["id"], "state": job.get("state", "running"), "started_at": utc_now(),
                              "cancel_requested": False, "again": run.again()}
            run.records.save(record)
            run.dispatching = None
            run.locks.hold_as(operation_id, "job", record["_keys"])
            if job.get("worker"):
                from .jobs import new_nonce, start_worker

                # Recorded before the worker starts, so the worker finds itself named (DCLI-065).
                record["_job"]["worker"] = {"nonce": new_nonce()}
                run.records.save(record)
                try:
                    started = start_worker(run, job["id"], record["_job"]["worker"]["nonce"])
                except OSError as exc:
                    failure = DriverError("E_UNKNOWN", f"The worker of long job {job['id']} could not start: {exc}. Nothing was sent.")
                    record["_job"].update(state="failed", ok=False, code=failure.code, message=failure.message,
                                          details=failure.details, finished_at=utc_now())
                    receipt["outcome"] = _outcome("not_started", "not_applied")
                    record.update(state="failed", receipt=receipt, finished_at=utc_now())
                    run.records.save(record)
                    run.locks.release(operation_id)
                    raise failure from None
                with run.mutex.hold():
                    latest = run.records.load(operation_id) or record
                    latest["_job"]["worker"] = {**latest["_job"].get("worker", {}), **started}
                    run.records.save(latest)
            # A job the runtime runs has no result yet; it comes with `job get` (DCLI-065).
            result = {} if job.get("worker") else self.safe_result(spec, out["facts"], Verification("not_applicable"), receipt)
            result = dict(result) if isinstance(result, dict) else {"value": result}
            result["job"] = {"id": job["id"], "state": job.get("state", "running"),
                             "poll": run.cmd("job get", [("--job", job["id"]), ("--wait", "60s")])}
            data = {"operation_id": operation_id, "receipt": slim_receipt(run, receipt), "result": result}
            fit_reply(run, data, record)
            return Result(data, self._receipt_untrusted(".receipt") + spec.untrusted_result())
        cause = error.code if error is not None else None
        ok, code = self.derive(receipt, cause)
        record["state"] = record_state(receipt, code)
        record.update(finished_at=utc_now(), _finished=time.time_ns())
        result = self.safe_result(spec, out["facts"], Verification(receipt["outcome"]["verification"], receipt["observed"]),
                                  receipt) if ok else None
        run.records.save(record)
        run.dispatching = None  # the outcome is recorded; nothing below may turn it into an unknown one
        if record["state"] == "unknown":
            run.locks.hold_as(operation_id, "unknown", record["_keys"])
        else:
            run.locks.release(operation_id)
        if ok:
            if isinstance(result, dict) and (spec.where.device or {}).get("simulated"):
                result = {**result, "simulated": True}  # results from a simulated device say so (DSEC-062)
            data = {"operation_id": operation_id, "receipt": slim_receipt(run, receipt), "result": slim_result(run, result)}
            fit_reply(run, data, record)
            return Result(data, self._receipt_untrusted(".receipt") + spec.untrusted_result())
        raise self.write_error(spec, record, receipt, code or "E_NATIVE_FAILURE", error, out.get("human"))

    @staticmethod
    def safe_result(spec: WriteSpec, facts: dict[str, Any], verification: Verification, receipt: dict[str, Any]) -> Any:
        """The command's own result. A failure to build it is noted in the receipt; it never
        replaces the outcome of a write that has happened."""
        try:
            return spec.result(facts, verification)
        except Exception as exc:  # noqa: BLE001
            receipt.setdefault("notes", []).append(f"The result could not be read after the write: {exc}")
            return {}

    def write_error(self, spec: WriteSpec, record: dict[str, Any], receipt: dict[str, Any], code: str,
                    cause: DriverError | None, human: DriverError | None) -> DriverError:
        run, operation_id = self.run, record["operation_id"]
        lookup = run.cmd("operation resolve", [("--id", operation_id)])
        untrusted = [p[1:] for p in self._receipt_untrusted(".details.receipt")]
        known = (human or cause).details.get("knowledge") if (human or cause) is not None else None
        first = f"Read `{run.cmd('knowledge get', [('--id', known[0])])}` first. " if known else ""
        if human is not None:
            # The change opened a dialog: a person deals with it, then the Agent settles the
            # outcome; it never sends the write again (DCLI-066).
            return DriverError(
                "E_HUMAN_REQUIRED", f"The change was sent, then the application asked for a person; it may have taken effect. {human.message}",
                dispatch_state="dispatched", action=human.details.get("action", "other"), resume=lookup, receipt=receipt,
                title=human.details.get("title"), native_message=human.details.get("native_message"), knowledge=known,
                next_action=f"{first}Ask the person to deal with the dialog, then run `{lookup}`; never send the write again.",
                untrusted=untrusted + ["message", "details.title", "details.native_message"],
            )
        if code == "E_OUTCOME_UNKNOWN":
            return DriverError(
                "E_OUTCOME_UNKNOWN", "The change was sent, but whether it took effect is not known. The target stays locked.",
                dispatch_state="unknown", lookup=lookup, receipt=receipt,
                next_action=f"Run `{lookup}` before anything else; never send the write again.", untrusted=untrusted,
            )
        dispatch_state = "not_started" if receipt["outcome"]["execution"] == "not_started" else "dispatched"
        messages = {
            "E_PARTIAL": "The change took effect only in part.",
            "E_VERIFY_FAILED": "The change took effect, but the read-back does not match the preview.",
            "E_VERIFY_INCOMPLETE": "The change took effect, but it could not be read back to verify it.",
        }
        extra: dict[str, Any] = {}
        message = messages.get(code)
        if cause is not None and message is None:
            message = cause.message
            extra = {k: v for k, v in cause.details.items() if k not in ("dispatch_state", "next_action")}
            untrusted += cause.untrusted
        details_next = {
            "E_VERIFY_FAILED": "Read `mismatched` in the receipt and tell the user what the application stored.",
            "E_VERIFY_INCOMPLETE": "Check the target with a read command before you report the change as done.",
        }.get(code)
        if details_next is None and "knowledge" in extra:
            details_next = cause.details["next_action"]
        return DriverError(code, message or code, dispatch_state=dispatch_state, receipt=receipt, untrusted=untrusted,
                           next_action=details_next, **extra)

    def _decorate(self, spec: WriteSpec, receipt: dict[str, Any], fresh: bool = True) -> bool:
        """Returns whether the receipt's document was read after the write."""
        if spec.session_ref():
            receipt["session"] = spec.session_ref()
        doc = spec.where.document
        now = None
        if doc is not None:
            now = self._fresh_document(spec) if fresh else None
            # Not read again: the revision before the write, and dirty unknown (DCLI-061).
            receipt["document"] = now or {"id": doc["id"], "revision": doc["revision"], "dirty": None}
        if self.run.fake or (spec.where.device or {}).get("simulated"):
            receipt["simulated"] = True
        return now is not None

    def _fresh_document(self, spec: WriteSpec) -> dict[str, Any] | None:
        doc = spec.where.document
        if doc is None:
            return None
        try:
            if spec.where.session is not None:
                docs = self.run.documents(spec.where.session["id"])
            else:
                docs = self.run.documents(None, doc.get("path"))
        except DriverError:
            return None
        for item in docs:
            if item.get("id") == doc["id"]:
                return {"id": item["id"], "revision": item["revision"], "dirty": item["dirty"]}
        return None

    @staticmethod
    def _receipt_untrusted(prefix: str) -> list[str]:
        return [f"{prefix}.target.label", f"{prefix}.notes", f"{prefix}.observed"]

    # ------------------------------------------------------------------ batches

    def run_batch(self, spec: WriteSpec, record: dict[str, Any], targets: list[dict[str, Any]],
                  changes: list[dict[str, Any]]) -> Result:
        """One child operation per object (DCLI-064)."""
        run, call = self.run, self.run.call
        assert call is not None
        batch_id = record["operation_id"]
        tier = record["preview"]["tier"] if "preview" in record else record["tier"]
        keep_going = call.values.get("--continue-on-error",
                                     self.contract["batch"]["continue_on_error_default_dangerous" if tier == "dangerous"
                                                            else "continue_on_error_default"])
        items: list[dict[str, Any]] = []
        children: list[str] = []
        first_error: DriverError | None = None
        stop_error: DriverError | None = None
        unknown_children: list[tuple[str, list[str]]] = []
        unfinished: list[int] = []
        stopped = False
        human: DriverError | None = None

        def settle(index: int, target: dict[str, Any], child: dict[str, Any], child_keys: set[str],
                   out: dict[str, Any], hung: bool = False) -> None:
            """Record one item's outcome; ``hung``: its read-back hangs, so nothing calls the adapter."""
            nonlocal first_error, stop_error, stopped, human
            child_id = child["operation_id"]
            receipt = out["receipt"]
            if not hung:
                run.settle_hang = lambda note: hung_batch(index, target, child, child_keys, out)
            try:
                read = self._decorate(spec, receipt, fresh=not hung)
            finally:
                if not hung:
                    run.settle_hang = None
            child["receipt"] = receipt
            _mark_unread(child, read)
            error = out["error"]
            if out["job"]:
                ok, code = True, None
                child["_job"] = {"id": out["job"]["id"], "state": out["job"].get("state", "running"),
                                 "started_at": utc_now(), "cancel_requested": False}
                run.locks.hold_as(child_id, "job", child_keys)
            elif out["unspend"]:
                ok, code = False, error.code if error else "E_NATIVE_FAILURE"
                child.update(state="failed", finished_at=utc_now())
                unfinished.append(index)
            else:
                ok, code = self.derive(receipt, error.code if error else None)
                child.update(state=record_state(receipt, code), finished_at=utc_now(), _finished=time.time_ns())
                if child["state"] == "unknown":
                    code = "E_OUTCOME_UNKNOWN"
                    unknown_children.append((child_id, child_keys))
                elif ok:
                    # What the change produced, as a call of one object returns it; its record keeps
                    # it for operation get when the batch's response has no room for it (DCLI-064).
                    made = self.safe_result(spec, out["facts"], Verification(receipt["outcome"]["verification"],
                                                                            receipt["observed"]), receipt)
                    if made not in (None, {}, []):
                        child["result"] = slim_result(run, made)
            if out.get("human") is not None and human is None:
                human = out["human"]
            run.records.save(child)
            children.append(child_id)
            item: dict[str, Any] = {"target": target["key"], "operation_id": child_id, "ok": ok}
            if "result" in child:
                item["result"] = child["result"]
            if not ok:
                item["code"] = code
                if first_error is None:
                    first_error = error or DriverError(code or "E_NATIVE_FAILURE", f"The write to {target['key']} failed.")
            items.append(item)
            if out["stop"]:
                stopped, stop_error = True, error or first_error
            elif not ok and not keep_going:
                stopped = True

        def hung_batch(index: int, target: dict[str, Any], child: dict[str, Any], child_keys: set[str],
                       out: dict[str, Any]) -> Result:
            # The process ends with the hang, so the objects after this one are not sent.
            nonlocal stopped, stop_error
            settle(index, target, child, child_keys, out, hung=True)
            for later in range(index + 1, len(targets)):
                items.append({"target": targets[later]["key"], "ok": False, "skipped": True})
                unfinished.append(later)
            if index + 1 < len(targets) and stop_error is None:
                stopped = True
                stop_error = first_error or DriverError("E_VERIFY_INCOMPLETE", "The read-back did not answer in time.")
            return self.end_batch(spec, record, items, children, unknown_children, unfinished, first_error, stop_error, human)

        for index, target in enumerate(targets):
            if stopped:
                items.append({"target": target["key"], "ok": False, "skipped": True})
                unfinished.append(index)
                continue
            child_id = run.records.new_id()
            child_changes = [c for c in changes if covers(target["key"], c["target"])]
            child = {"operation_id": child_id, "command": spec.command.path, "target": target, "batch": batch_id,
                     "effects": list(spec.command.effects), "tier": tier, "scope_id": record["scope_id"],
                     "state": "dispatched", "planned_at": record["planned_at"], "dispatched_at": utc_now(),
                     "preview": {**record["preview"], "operation_id": child_id, "target": target,
                                 "changes": self.mask(spec, copy.deepcopy(child_changes[: self.contract["preview"]["max_changes"]])),
                                 "summary": {"total": len(child_changes), "by_origin": {"tool": len(child_changes)} if child_changes else {},
                                             "by_kind": {target["kind"]: len(child_changes)} if child_changes else {}}},
                     "_call": record.get("_call"), "_request": spec.stored_request(index),
                     "_tolerances": spec.tolerances(), "_seq": time.time_ns(),
                     "_changes": self.mask(spec, [copy.deepcopy(c) for c in child_changes if c.get("origin", "tool") == "tool"])}
            child["preview"].pop("changes_truncated", None)
            child_keys = _keys([target], child_changes)
            child["_keys"] = sorted(child_keys)  # stored before dispatch, so a crash leaves them locked
            if named_files([], [target]):
                child["_files"] = named_files([], [target])
            run.records.save(child)
            out = self.one(spec, child, target, child_changes, index,
                           on_hung=lambda out, i=index, t=target, c=child, k=child_keys: hung_batch(i, t, c, k, out))
            settle(index, target, child, child_keys, out)
        return self.end_batch(spec, record, items, children, unknown_children, unfinished, first_error, stop_error, human)

    def end_batch(self, spec: WriteSpec, record: dict[str, Any], items: list[dict[str, Any]], children: list[str],
                  unknown_children: list[tuple[str, list[str]]], unfinished: list[int], first_error: DriverError | None,
                  stop_error: DriverError | None, human: DriverError | None) -> Result:
        """Record the batch and report it (DCLI-064)."""
        run = self.run
        batch_id = record["operation_id"]
        summary = {"total": len(items), "succeeded": sum(1 for i in items if i["ok"]),
                   "failed": sum(1 for i in items if not i["ok"] and not i.get("skipped") and i.get("code") != "E_OUTCOME_UNKNOWN"),
                   "unknown": sum(1 for i in items if i.get("code") == "E_OUTCOME_UNKNOWN"),
                   "skipped": sum(1 for i in items if i.get("skipped"))}
        record["_children"] = children
        record["finished_at"] = utc_now()
        record["state"] = "unknown" if summary["unknown"] else ("completed" if summary["succeeded"] else "failed")
        run.records.save(record)
        run.locks.replace(batch_id, unknown_children)
        run.dispatching = None
        batch = {"operation_id": batch_id, "items": items, "summary": summary}
        # The items' results take the room the response has left; the rest stay in the records.
        room = int(run.contract["envelope"]["limits"]["success_chars"]) * 3 // 4
        with_result = [i for i in items if "result" in i]
        while with_result and len(json.dumps(batch, ensure_ascii=False)) > room:
            with_result.pop().pop("result")
            batch["more_results"] = run.cmd("operation get", [("--id", "<operation_id>")]) + " gives the result of an item that shows none."
        untrusted = [".items[*]" + p for p in spec.untrusted_result()] + (
            [".more_results"] if "more_results" in batch else [])
        if summary["succeeded"] == summary["total"]:
            fit_reply(run, batch, record)
            return Result(batch, untrusted)
        lookup = run.cmd("operation list", [("--state", "unknown"), ("--batch", batch_id)])
        if summary["unknown"]:
            extra_unknown: dict[str, Any] = {}
            ask = ""
            if human is not None:
                # A change opened a dialog: a person must deal with it first (DCLI-066).
                extra_unknown = {k: human.details.get(k) for k in ("action", "title") if human.details.get(k)}
                ask = "Ask the person to deal with the dialog in the application first. "
            raise DriverError("E_OUTCOME_UNKNOWN", f"{summary['unknown']} objects of the batch may or may not have changed; they stay locked.",
                              dispatch_state="unknown", lookup=lookup, batch=batch,
                              untrusted=["details.title"] if "title" in extra_unknown else [],
                              next_action=f"{ask}Run `{lookup}` and settle each one with operation resolve; never send them again.",
                              **extra_unknown)
        outcomes = {i["operation_id"]: (run.records.load(i["operation_id"]) or {}).get("receipt", {}).get("outcome", {})
                    for i in items if i.get("operation_id")}
        took_effect = summary["succeeded"] > 0 or any(
            o.get("change") in ("applied", "partial") or o.get("persistence") == "saved" or o.get("delivery") == "delivered"
            for o in outcomes.values())
        extra: dict[str, Any] = {}
        if stop_error is not None and unfinished:
            extra["resume"] = self.resume_batch(spec, [spec.selected[i] for i in unfinished])
        if took_effect:
            raise DriverError("E_PARTIAL", f"{summary['succeeded']} of {summary['total']} objects changed; read details.batch.",
                              dispatch_state="dispatched", batch=batch, untrusted=["details.batch" + p for p in untrusted],
                              next_action="Read details.batch, then send again only the objects that failed.", **extra)
        cause = stop_error or first_error
        assert cause is not None
        details = {k: v for k, v in cause.details.items() if k not in ("dispatch_state", "next_action", "receipt")}
        details.update(extra)
        untouched = all(o.get("execution") == "not_started" for o in outcomes.values())
        raise DriverError(cause.code, f"No object of the batch changed: {cause.message}", batch=batch,
                          dispatch_state="not_started" if untouched else "dispatched",
                          untrusted=[u for u in cause.untrusted if not u.startswith("details.receipt")], **details)

    def resume_batch(self, spec: WriteSpec, ids: list[str]) -> str:
        """The command that covers only the unfinished objects (DCLI-064)."""
        run, call = self.run, self.run.call
        assert call is not None
        if spec.items is not None:
            folder = run.state.sub("cache")
            path = folder / f"resume-{secrets.token_hex(6)}.json"
            secret = {f.key for f in spec.command.flags if f.secret} | set(spec.secrets)
            hide = lambda obj: {k: "<secret>" if k in secret else v for k, v in obj.items()}  # noqa: E731 - DSEC-040
            data = hide({k: v for k, v in call.values["--input"].items() if k != "items"})
            data["items"] = [hide(i) for n, i in enumerate(call.values["--input"]["items"])
                             if str(i.get("id", f"#{n + 1}")) in ids]
            path.write_text(json.dumps(data), encoding="utf-8")
            return run.again(drop=("--input", "--confirm", "--dry-run")) + " --input " + quote(str(path))
        return run.again(drop=("--ids", "--confirm", "--dry-run")) + " --ids " + quote(",".join(ids))


def _keys(targets: list[dict[str, Any]], changes: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for key in [t["key"] for t in targets] + [c["target"] for c in changes]:
        if key not in keys:
            keys.append(key)
    return keys


def _outcome(execution: str, change: str, delivery: str = "not_applicable", persistence: str = "not_applicable",
             verification: str = "not_applicable") -> dict[str, str]:
    return {"execution": execution, "change": change, "delivery": delivery, "persistence": persistence,
            "verification": verification}


def record_state(receipt: dict[str, Any], code: str | None) -> str:
    """The record state follows the derived code: an outcome-unknown write stays ``unknown``
    until ``operation resolve`` settles it (DCLI-063)."""
    if code == "E_OUTCOME_UNKNOWN":
        return "unknown"
    return "completed" if receipt["outcome"]["execution"] == "completed" else "failed"


def changes_digest(changes: list[dict[str, Any]] | None) -> str | None:
    """A digest of the changes a preview showed, before and after, so a token cannot run a
    change the person did not see (DCLI-055)."""
    if changes is None:
        return None
    rows = sorted(json.dumps([c.get("target"), c.get("field"), c.get("before"), c.get("after"), c.get("origin", "tool")],
                             sort_keys=True, default=str) for c in changes)
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _secret_digests(values: dict[str, str], salt: str) -> dict[str, Any]:
    """Secrets are bound to a token by a salted, slow hash, never stored in clear (DSEC-040)."""
    digests = {k: hashlib.pbkdf2_hmac("sha256", v.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()
               for k, v in sorted(values.items())} if salt else {}
    return {"salt": salt, "digests": digests}


def _differs(old: dict[str, Any], new: dict[str, Any], command: Command) -> str | None:
    """The first parameter whose value differs from the previewed call, as its flag name."""
    names = {}
    for flag in command.flags:
        names[flag.key] = flag.name
        if flag.quantity:
            names[flag.key + "_" + flag.quantity] = flag.name
    for section in ("document", "device"):
        if old.get(section) != new.get(section):
            return {"document": "--doc", "device": "--device"}[section]
    a, b = old.get("inputs", {}), new.get("inputs", {})
    for key in list(a) + [k for k in b if k not in a]:
        if a.get(key, _MISSING) != b.get(key, _MISSING):
            return _flag_for(key, command)
    return None


_MISSING = object()


def _flag_for(key: str, command: Command) -> str:
    for flag in command.flags:
        if key == flag.key or key.startswith(flag.key + "_"):
            return flag.name
    return "--input" if key == "items" else f"--{key.replace('_', '-')}"


def conflict(name: str, message: str, next_action: str, **extra: Any) -> DriverError:
    return DriverError("E_CONFLICT", message, conflict=name, next_action=next_action, **extra)
