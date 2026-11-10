"""One run of the CLI: the state directory, the adapter calls, and choosing the session, the
document and the device a command acts on (DCLI-201, DCLI-202, DCLI-204, DCLI-211, DCLI-230).
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .adapter import AdapterStore, Backend, BadProtocol, BadReply, CapabilityError, Lost, Unanswered
from .args import Call, command_line, quote
from .errors import NEXT_ACTION, DriverError, usage
from .records import Locks, Mutex, Records, utc_now
from .registry import Command
from .scope import Scope, load as load_scope
from .state import StateDir
from .targets import file_key, normalize

if TYPE_CHECKING:
    from .tool import Tool

SESSION_DEADLINE_MS = 2000  # per instance, for the session reads (DCLI-205)
READ_DEADLINE_MS = 30000
WRITE_DEADLINE_MS = 45000  # a call that does work returns within 60 s (DCLI-065)


@dataclass
class Where:
    """What a command acts on."""

    mode: str
    session: dict[str, Any] | None = None
    document: dict[str, Any] | None = None
    device: dict[str, Any] | None = None


@dataclass
class Inputs:
    """The inputs of a domain command: for the adapter and for the token."""

    adapter: dict[str, Any]
    bound: dict[str, Any]
    selected: list[str]  # the object IDs selected with --id, --ids or --input items
    batch: bool
    items: list[dict[str, Any]] | None = None
    secrets: dict[str, str] | None = None  # secret inputs, bound to the token by a salted hash only


class Run:
    def __init__(self, tool: Tool, call: Call | None, env: Mapping[str, str]) -> None:
        self.tool = tool
        self.contract = tool.contract
        self.call = call
        self.env = env
        prefix = tool.prefix
        self.worker = env.get(f"{prefix}_JOB_WORKER")  # set only in the worker of a long job (DCLI-065)
        # The worker of a long job has the long deadline for every call it makes (DCLI-065).
        long_ms = int(tool.contract["jobs"]["worker_max_seconds"]) * 1000
        self.write_deadline_ms = long_ms if self.worker else WRITE_DEADLINE_MS
        self.read_deadline_ms = long_ms if self.worker else READ_DEADLINE_MS
        state = env.get(f"{prefix}_STATE_DIR") or str(Path.home() / f".{tool.name}")
        self.state = StateDir(state)
        self.records = Records(self.state, self.contract["operation"]["retention_days"], self.contract["operation"]["id_prefix"])
        self.mutex = Mutex(self.state)
        self.locks = Locks(self.state, self.records, self.mutex, tool.name)
        self.scope_path = Path(env.get(f"{prefix}_SCOPE_FILE") or str(self.state.path / "scope.json"))
        self.fake = env.get(f"{prefix}_FAKE_HOST") or None
        self.notices: list[dict[str, str]] = []
        self.resolved: dict[str, str] = {}  # target flags filled in by the runtime, for generated commands
        self.dispatching: str | None = None  # the operation whose change may be in flight
        self.on_hang: Any = None  # set by the tool: reports a hung in-process adapter call and exits
        # Set while a read follows a write whose apply replied: settles it with the note given,
        # before the hang ends the process (DCLI-278).
        self.settle_hang: Any = None
        self._sessions: dict[str, Any] | None = None
        self._devices: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------ plumbing

    @cached_property
    def backend(self) -> Backend:
        store = AdapterStore(self.state)
        backend = Backend(
            self.tool.adapter_class(store), self.contract.capabilities, host_component=self.tool.host_component,
            fake_address=self.fake, hosts_dir=str(self.state.path / "hosts"), cli_version=self.tool.version,
            protocol_version=self.contract["host_protocol"]["version"],
        )
        backend.on_hang = self.on_hang
        return backend

    @cached_property
    def scope(self) -> Scope:
        return load_scope(self.scope_path, self.contract)

    def close(self) -> None:
        if "backend" in self.__dict__:
            self.backend.close()

    def cmd(self, path: str, args: list[tuple[str, Any]]) -> str:
        return command_line(self.tool.name, path, args)

    def again(self, *, confirm: str | None = None, dangerous: bool = False, drop: tuple[str, ...] = ()) -> str:
        """The current command as a complete line, with the targets the runtime chose (DCLI-011)."""
        call = self.call
        assert call is not None
        args: list[tuple[str, Any]] = []
        for flag in ("--session", "--doc", "--device", "--job"):
            value = call.raw.get(flag) or self.resolved.get(flag)
            if value and call.command.flag(flag) and flag not in drop:
                args.append((flag, value))
        for name in call.given:
            if name in ("--session", "--doc", "--device", "--job") or name in drop:
                continue
            flag = call.command.flag(name)
            if flag is not None and flag.secret:
                args.append((name, "<secret>"))  # a secret never goes into a generated command (DSEC-040)
                continue
            args.append((name, True if flag and flag.type == "boolean" and call.values[name] else call.raw[name]))
        if confirm:
            args.append(("--confirm", confirm))
        if dangerous:
            args.append(("--dangerous", True))
        return self.cmd(call.command.path, args)

    # ------------------------------------------------------------------ adapter calls for reads

    def capability(self, cap: str, request: dict[str, Any], deadline_ms: int | None = None) -> Any:
        session = request.get("session")
        if not self.backend.has(cap, session):
            raise DriverError("E_UNSUPPORTED", f"This tool's adapter has no {cap} capability, so it cannot do this.")
        try:
            reply = self.backend.call(cap, request, deadline_ms or self.read_deadline_ms)
        except CapabilityError as exc:
            raise self.host_error(exc, "not_started") from None
        except BadReply as exc:
            raise DriverError("E_PROTOCOL", f"The reply of the application cannot be understood: {exc}") from None
        except Unanswered as exc:
            if exc.crashed:
                raise DriverError("E_UNKNOWN", f"The adapter failed: {exc}") from None
            if session:
                raise DriverError("E_BUSY", f"Session {session} did not answer in time and is unresponsive; nothing was sent.",
                                  retry_after_ms=SESSION_DEADLINE_MS) from None
            raise DriverError("E_BACKEND_UNAVAILABLE", f"The application did not answer: {exc}") from None
        except Lost as exc:
            raise DriverError("E_BACKEND_UNAVAILABLE", f"{exc}; the session is lost.") from None
        except BadProtocol as exc:
            raise DriverError("E_PROTOCOL", f"The host component cannot be used: {exc}. Nothing was sent.") from None
        self.take_notices(reply)
        return reply

    def take_notices(self, reply: Any) -> None:
        """Notices the adapter adds to a reply go to ``meta.notices``, their text marked
        untrusted because it may quote the application (DCLI-004, DCLI-260)."""
        notices = reply.get("notices") if isinstance(reply, dict) else None
        for notice in notices if isinstance(notices, list) else []:
            if isinstance(notice, dict) and notice.get("severity") in ("info", "warning") \
                    and isinstance(notice.get("message"), str) and notice["message"]:
                entry = {"type": "application", "severity": notice["severity"], "message": notice["message"][:300],
                         "_untrusted": True}
                kept = [n for n in self.notices if n.get("type") == "application"]
                # Each item of a batch may say the same: one copy, and a few notices at most.
                if entry not in kept and len(kept) < 5:
                    self.notices.append(entry)

    def host_error(self, exc: CapabilityError, dispatch_state: str, write: bool = False) -> DriverError:
        """Turn an error the adapter or a host reported into the runtime's error (DCLI-279)."""
        data = exc.data
        code = data.get("code")
        if code not in self.contract["host_protocol"]["allowed_error_codes"]:
            return DriverError("E_PROTOCOL", f"The adapter answered with an error it may not use: {code}.",
                               dispatch_state=dispatch_state)
        # A read sends no change, so only a write's reply must say that nothing was sent.
        if code in ("E_AMBIGUOUS", "E_CONFLICT", "E_BACKEND_UNAVAILABLE", "E_TIMEOUT") and write and \
                exc.dispatch_state != "not_started":
            return DriverError("E_PROTOCOL", f"The adapter answered {code} after sending the change; it comes only before.",
                               dispatch_state=dispatch_state)
        if code == "E_TIMEOUT" and write:
            return DriverError("E_PROTOCOL", "The adapter answered E_TIMEOUT to a write; it belongs to reads.",
                               dispatch_state=dispatch_state)
        extra: dict[str, Any] = {}
        untrusted = []
        if code == "E_AMBIGUOUS":
            # The adapter found several objects for one name; the Agent picks from them (DCLI-012).
            candidates = data.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 2 or not all(isinstance(c, dict) for c in candidates):
                return DriverError("E_PROTOCOL", "The adapter answered E_AMBIGUOUS without at least two candidates.",
                                   dispatch_state=dispatch_state)
            extra["candidates"] = candidates
            untrusted.append("details.candidates")
        if code == "E_CONFLICT":
            if data.get("conflict") not in self.contract["conflict_values"]:
                return DriverError("E_PROTOCOL", f"The adapter answered E_CONFLICT with an unknown conflict: {data.get('conflict')}.",
                                   dispatch_state=dispatch_state)
            extra["conflict"] = data["conflict"]
        if isinstance(data.get("native_message"), str):
            extra["native_message"] = data["native_message"]
            untrusted.append("details.native_message")
        if code == "E_BUSY":
            extra["retry_after_ms"] = int(data.get("retry_after_ms", 1000))
        if code == "E_HUMAN_REQUIRED":
            action = data.get("action")
            extra["action"] = action if action in self.contract["human_required_actions"] else "other"
            extra["resume"] = self.again(confirm=self.call.confirm, dangerous=self.call.dangerous) if self.call else self.tool.name
            untrusted.append("details.resume")
            if isinstance(data.get("title"), str):
                extra["title"] = data["title"]
                untrusted.append("details.title")
        next_action = None
        ids = self.knowledge_ids(data)
        if ids:
            # An error the adapter recognized points to the entries that tell how to handle it (DSKL-124).
            extra["knowledge"] = ids
            next_action = self.knowledge_first(ids, NEXT_ACTION[code])
        message = str(exc) or code
        untrusted.append("message")
        return DriverError(code, message, next_action=next_action, dispatch_state=dispatch_state, untrusted=untrusted, **extra)

    # ------------------------------------------------------------------ sessions

    def sessions(self) -> dict[str, Any]:
        """``sessions`` with the state of each host component overlaid (DCLI-205, DCLI-278). The
        host components are probed while the adapter answers, so the two share the time."""
        if self._sessions is not None:
            return self._sessions
        probe = self.backend.start_probe(SESSION_DEADLINE_MS) if self.tool.host_component else None
        result = self.capability("sessions", {}, int(self.contract["session_read_deadline"]["adapter_sessions_ms"]))
        items = [s for s in result.get("items", []) if s.get("state") != "closed"]
        if probe is not None:
            probed = probe()
            for item in items:
                status = probed.get(item["id"], {}).get("status")
                if status == "lost":
                    item["state"] = "lost"
                    item.pop("blocker", None)
                elif status == "unresponsive" and item["state"] != "unresponsive":
                    item["state"] = "unresponsive"
                    item.pop("blocker", None)
                    item["since_at"] = utc_now()
        self._sessions = {"items": items, "complete": bool(result.get("complete", True))}
        return self._sessions

    def has_sessions(self) -> bool:
        return "session" in self.tool.modes and self.backend.has("sessions")

    def summary(self, session: dict[str, Any]) -> dict[str, Any]:
        out = {"id": session["id"], "state": session["state"], "documents": session.get("documents", [])}
        if "blocker" in session:
            out["blocker"] = session["blocker"]
        return out

    def ambiguous_sessions(self, items: list[dict[str, Any]], what: str) -> DriverError:
        return DriverError(
            "E_AMBIGUOUS", f"{what}; name one with --session.",
            candidates=[self.summary(s) for s in items],
            untrusted=["details.candidates[*].blocker.title", "details.candidates[*].documents[*].path"],
            next_action="Pick one of `candidates` only when the user named it, then run the command again with --session.",
        )

    def session(self, session_id: str | None) -> dict[str, Any]:
        """The instance a command acts on (DCLI-202)."""
        items = self.sessions()["items"]
        if session_id:
            for item in items:
                if item["id"] == session_id:
                    return item
            raise DriverError("E_NOT_FOUND", f"No running instance has the session ID {session_id}.",
                              next_action=f"Run `{self.tool.name} session list` to see the running instances.")
        if len(items) == 1:
            self.resolved["--session"] = items[0]["id"]
            return items[0]
        if not items:
            hint = (f"Start one with `{self.tool.name} session open`, or ask the user to start the application."
                    if self.backend.has("open_session") else "Ask the user to start the application.")
            raise DriverError("E_BACKEND_UNAVAILABLE", "No instance of the application is running.", next_action=hint)
        raise self.ambiguous_sessions(items, f"{len(items)} instances are running")

    def knowledge_ids(self, data: Any) -> list[str]:
        """The knowledge entries the adapter named for an error, a blocked preview or a dialog (DSKL-124)."""
        ids = data.get("knowledge") if isinstance(data, dict) else None
        pattern = self.contract["knowledge"]["id_pattern"]
        if isinstance(ids, list) and ids and all(isinstance(i, str) and re.fullmatch(pattern, i) for i in ids):
            return list(ids)
        return []

    def knowledge_first(self, ids: list[str], then: str) -> str:
        return f"Read `{self.tool.name} knowledge get --id {ids[0]}` first. {then}"

    def gate_session(self, session: dict[str, Any], halt: bool = False) -> None:
        """React to the session state as DCLI-204 sets; a halt command passes busy and blocked (DSEC-018)."""
        state, sid = session["state"], session["id"]
        if state in ("ready",):
            return
        if state == "closed":
            raise DriverError("E_NOT_FOUND", f"Session {sid} has ended.")
        if state == "lost":
            raise DriverError("E_BACKEND_UNAVAILABLE", f"Session {sid} is lost: its host component does not accept connections. Nothing was sent.")
        if state == "unreachable":
            raise DriverError("E_BACKEND_UNAVAILABLE", f"Session {sid} is running, but this tool cannot drive it. Nothing was sent.",
                              next_action="Work in a session that is ready, or ask the person which instance to use.")
        if halt:
            return
        if state in ("starting", "busy", "unresponsive"):
            reason = {"starting": "is starting", "busy": "is busy", "unresponsive": "does not respond"}[state]
            raise DriverError("E_BUSY", f"Session {sid} {reason}; nothing was sent.", retry_after_ms=SESSION_DEADLINE_MS)
        if state == "blocked":
            blocker = session.get("blocker") or {"action": "other", "title": ""}
            ids = self.knowledge_ids(blocker)
            extra: dict[str, Any] = {"knowledge": ids, "next_action": self.knowledge_first(ids, NEXT_ACTION["E_HUMAN_REQUIRED"])} if ids else {}
            raise DriverError(
                "E_HUMAN_REQUIRED", f"Session {sid} is blocked by a dialog or a similar window; nothing was sent.",
                action=blocker.get("action", "other"), title=blocker.get("title", ""),
                resume=self.again(confirm=self.call.confirm if self.call else None,
                                  dangerous=bool(self.call and self.call.dangerous)),
                untrusted=["details.title", "details.resume"], **extra,
            )

    # ------------------------------------------------------------------ documents

    def doc_key(self, doc: dict[str, Any]) -> str:
        """``file:///<path>``, or the ``untitled:`` key only the adapter knows (DCLI-210)."""
        if doc.get("path"):
            return file_key(doc["path"])
        if isinstance(doc.get("key"), str) and doc["key"].startswith("untitled:"):
            return doc["key"]
        raise DriverError("E_PROTOCOL", f"The adapter gave no untitled: key for the untitled document {doc.get('id')}.")

    def documents(self, session_id: str | None = None, path: str | None = None,
                  deadline_ms: int = SESSION_DEADLINE_MS) -> list[dict[str, Any]]:
        request: dict[str, Any] = {"session": session_id} if session_id else {}
        if path:
            request["path"] = path
        return list(self.capability("documents", request, deadline_ms).get("items", []))

    @staticmethod
    def _matches(doc: dict[str, Any], ref: str) -> bool:
        if doc.get("id") == ref:
            return True
        return bool(doc.get("path")) and normalize(file_key(doc["path"])) == normalize(file_key(ref))

    def holders(self, ref: str) -> list[dict[str, Any]]:
        """The instances that have a document open, by ID or path."""
        if not self.has_sessions():
            return []
        return [s for s in self.sessions()["items"] if any(self._matches(d, ref) for d in s.get("documents", []))]

    def where(self, command: Command, need_document: bool = True) -> Where:
        """Choose the mode, the session, the document and the device of a call (DCLI-201, DCLI-202),
        and refuse a document of a kind the command does not work on (DCLI-210)."""
        where = self._where(command, need_document)
        doc = where.document
        if command.doc_kinds and doc is not None:
            kind = doc.get("kind")
            if kind is None and where.session is not None and where.session.get("state") != "ready":
                return where  # the session gate refuses the call first (DCLI-204)
            if kind is None:
                raise DriverError("E_PROTOCOL", f"The adapter reports no kind for document {doc.get('id')}, "
                                                f"and {command.path} works only on {', '.join(command.doc_kinds)}.")
            if kind not in command.doc_kinds:
                raise DriverError("E_VALIDATION", f"{command.path} works on {', '.join(command.doc_kinds)}, "
                                                  f"not on the {kind} {doc.get('path') or doc.get('id')}; nothing was sent.",
                                  untrusted=["message"],
                                  next_action=f"Run `{self.tool.name} reference --command {quote(command.path)}` to see what it works on.")
        return where

    def _where(self, command: Command, need_document: bool = True) -> Where:
        call = self.call
        assert call is not None
        modes = [m for m in command.modes if m in self.tool.modes]
        session_ref = call.values.get("--session")
        doc_ref = call.values.get("--doc")
        device_ref = call.values.get("--device")
        if modes == ["device"] or (device_ref and "device" in modes):
            return Where("device", device=self.device(device_ref))
        if session_ref and "session" in modes:
            session = self.session(session_ref)
            return Where("session", session=session, document=self._session_doc(session, doc_ref, need_document))
        if doc_ref:
            holders = self.holders(doc_ref) if "session" in self.tool.modes else []
            if len(holders) > 1:
                raise self.ambiguous_sessions(holders, f"{len(holders)} instances have {doc_ref} open")
            if holders and "session" in modes:
                self.resolved["--session"] = holders[0]["id"]
                return Where("session", session=holders[0], document=self._session_doc(holders[0], doc_ref, True))
            if "file" in modes:
                if holders and any(e in ("change", "persist") for e in command.effects):
                    raise DriverError(
                        "E_CONFLICT", f"Session {holders[0]['id']} has {doc_ref} open; a write to the file would undo its changes.",
                        conflict="file_open_in_session",
                        next_action=f"Run the command on the session with --session {holders[0]['id']}, or close the document there first.",
                    )
                if holders:
                    # A write that only makes other files from the document may go ahead; it says
                    # that it used the file on disk, not the instance's unsaved state (DCLI-201).
                    open_doc = next((d for d in holders[0].get("documents", []) if self._matches(d, doc_ref)), {})
                    if open_doc.get("dirty") is not False:
                        self.notices.append({"type": "document", "severity": "warning",
                                             "message": f"Session {holders[0]['id']} has {doc_ref} open and may have unsaved changes; "
                                                        "this call used the file on disk."})
                return Where("file", document=self._file_doc(doc_ref))
            raise DriverError("E_NOT_FOUND", f"No running instance has the document {doc_ref} open.",
                              next_action=f"Run `{self.tool.name} doc list` to see the open documents.")
        if "session" in modes:
            session = self.session(None)
            return Where("session", session=session, document=self._session_doc(session, None, need_document))
        if "file" in modes:
            raise usage(f"{command.path} needs --doc: the path of the file.", "--doc")
        raise usage(f"{command.path} needs a target.", None)

    def _session_doc(self, session: dict[str, Any], ref: str | None, need: bool) -> dict[str, Any] | None:
        if not need and not ref:
            return None
        if session["state"] != "ready" and not ref:
            # The state gate refuses the call; name the document in its resume command (DCLI-011).
            summary = session.get("documents", [])
            if len(summary) == 1:
                self.resolved["--doc"] = summary[0]["id"]
            return None
        docs = self.documents(session["id"]) if session["state"] == "ready" else session.get("documents", [])
        if ref:
            for doc in docs:
                if self._matches(doc, ref):
                    self.resolved["--doc"] = doc["id"]
                    return doc
            raise DriverError("E_NOT_FOUND", f"Session {session['id']} has no document {ref} open.",
                              next_action=f"Run `{self.tool.name} doc list --session {session['id']}` to see its documents.")
        if len(docs) == 1:
            self.resolved["--doc"] = docs[0]["id"]
            return docs[0]
        if not docs:
            return None
        raise DriverError(
            "E_AMBIGUOUS", f"Session {session['id']} has {len(docs)} documents open; name one with --doc.",
            candidates=[{"id": d["id"], "path": d.get("path"), "dirty": d.get("dirty")} for d in docs],
            untrusted=["details.candidates[*].path"],
            next_action="Pick one of `candidates` only when the user named it, then run the command again with --doc.",
        )

    def _file_doc(self, ref: str) -> dict[str, Any]:
        path = os.path.abspath(ref)
        for doc in self.documents(None, path, READ_DEADLINE_MS):
            if self._matches(doc, path):
                self.resolved["--doc"] = path
                return doc
        raise DriverError("E_NOT_FOUND", f"There is no document at {path}.")

    def gate_document(self, doc: dict[str, Any] | None, saving: bool = False) -> None:
        """React to the disk state and read-only flag of a document (DCLI-211)."""
        if doc is None:
            return
        disk = doc.get("disk", "in_sync")
        if disk == "changed":
            raise DriverError("E_CONFLICT", f"Someone else changed {doc.get('path')} on disk; nothing was sent.",
                              conflict="file_changed_on_disk", untrusted=["message"],
                              next_action="Ask the user how to reconcile the file on disk with the open document.")
        if disk == "missing" and saving:
            raise DriverError("E_CONFLICT", f"The file {doc.get('path')} no longer exists; nothing was saved.",
                              conflict="file_missing", untrusted=["message"],
                              next_action="Save the document under a new path with doc save-as, or ask the user.")
        if doc.get("readonly"):
            raise DriverError("E_VALIDATION", f"Document {doc['id']} is read-only; nothing was sent.")

    # ------------------------------------------------------------------ devices

    def devices(self) -> list[dict[str, Any]]:
        if self._devices is None:
            self._devices = list(self.capability("devices", {}).get("items", []))
        return self._devices

    def device(self, ref: str | None) -> dict[str, Any]:
        items = self.devices()
        if ref:
            for item in items:
                if item["id"] == ref:
                    return item
            raise DriverError("E_NOT_FOUND", f"There is no device {ref}.",
                              next_action=f"Run `{self.tool.name} device list` to see the devices.")
        if len(items) == 1:
            self.resolved["--device"] = items[0]["id"]
            return items[0]
        if not items:
            raise DriverError("E_BACKEND_UNAVAILABLE", "No device is connected.")
        raise DriverError("E_AMBIGUOUS", f"{len(items)} devices are connected; name one with --device.",
                          candidates=[{"id": d["id"], "model": d.get("model"), "state": d.get("state")} for d in items])

    def gate_device(self, device: dict[str, Any], halt: bool = False) -> None:
        """React to the device state (DCLI-230, DSEC-061); a halt command runs unless offline."""
        state, did = device["state"], device["id"]
        if state == "ready":
            return
        if state == "offline":
            raise DriverError("E_BACKEND_UNAVAILABLE", f"Device {did} cannot be reached; nothing was sent.")
        if halt:
            return
        if state == "busy":
            raise DriverError("E_BUSY", f"Device {did} is busy; nothing was sent.", retry_after_ms=1000)
        if state in ("fault", "emergency_stop"):
            what = "reports a fault" if state == "fault" else "is in emergency stop"
            raise DriverError(
                "E_HUMAN_REQUIRED", f"Device {did} {what}; a person must check it on site. Nothing was sent.",
                action="confirm_on_device",
                resume=self.again(confirm=self.call.confirm if self.call else None, dangerous=bool(self.call and self.call.dangerous)),
                untrusted=["details.resume"],
            )

    def fresh_device(self, device_id: str) -> dict[str, Any]:
        """Read the device state again, never from a cache (DSEC-061)."""
        self._devices = None
        return self.device(device_id)

    # ------------------------------------------------------------------ inputs

    def inputs(self, command: Command) -> Inputs:
        """The inputs of a domain command, converted to standard units (DCLI-082, adapter.schema.json)."""
        call = self.call
        assert call is not None
        flags = {f.name: f for f in command.flags if f.name not in ("--session", "--doc", "--device") and not f.runtime}
        given = [n for n in call.given if n in flags]
        adapter: dict[str, Any] = {}
        items = None
        if "--input" in given:
            others = [n for n in given if n != "--input"]
            if others:
                raise usage(f"Give the parameters either as flags or in --input, not both ({', '.join(others)}).", others[0])
            adapter, items = self._input_object(command, call.values["--input"])
        else:
            for name in given:
                flag = flags[name]
                adapter[self.key(flag)] = call.values[name]
        for flag in flags.values():
            if flag.required and self.key(flag) not in adapter and not (items and all(self.key(flag) in i for i in items)):
                if not (flag.name == "--id" and ("ids" in adapter or items)):
                    raise usage(f"{command.path} needs {flag.name}.", flag.name)
        select = [k for k in ("id", "ids") if k in adapter] + (["items"] if items is not None else [])
        if len(select) > 1:
            raise usage("Select the objects in one way only: --id, --ids or --input items.", "--ids")
        selected: list[str] = []
        if "id" in adapter:
            selected = [str(adapter["id"])]
        elif "ids" in adapter:
            selected = list(adapter["ids"])
        elif items is not None:
            # An item of a create command may lack an id: it makes a new object (DCLI-064).
            selected = [str(i["id"]) if "id" in i else f"#{n + 1}" for n, i in enumerate(items)]
        changed = [k for k in adapter if k not in ("id", "ids", "items")] + [k for i in items or [] for k in i if k != "id"]
        if command.verb == "update" and not changed:
            raise usage(f"{command.path} changes only the properties given, and none is given.", None)
        bound = dict(adapter)
        secrets: dict[str, str] = {}
        for flag in flags.values():
            key = self.key(flag)
            if key not in adapter:
                continue
            if flag.secret:
                secrets[key] = str(adapter[key])
                bound[key] = {"secret": True}  # the value is bound by a salted hash in the token (DSEC-040)
            elif flag.type == "path" and not flag.output:
                # The token binds the file's content too, so a changed input file needs a new preview.
                bound[key] = {"path": adapter[key], **_file_digest(adapter[key], flag.name)}
        batch = "ids" in adapter or items is not None
        return Inputs(adapter, bound, selected, batch, items, secrets)

    def key(self, flag: Any) -> str:
        if flag.name == "--input":
            return "input"
        suffix = self.contract.units[flag.quantity]["suffix"] if flag.quantity else ""
        return flag.key + suffix

    def _input_object(self, command: Command, data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
        """Normalize an --input object: keys as flag names, values with units (DCLI-026, DCLI-064)."""
        by_key: dict[str, Any] = {}
        for flag in command.flags:
            if flag.name in ("--input", "--session", "--doc", "--device"):
                continue
            by_key[flag.key] = flag
            if flag.quantity:
                by_key[self.key(flag)] = flag

        def norm(obj: dict[str, Any], where: str, item: bool) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for key, value in obj.items():
                if item and key == "id":
                    if not isinstance(value, (str, int)) or isinstance(value, bool) or str(value) == "":
                        raise usage(f"{where}: id must be a string.", "--input")
                    out["id"] = str(value)
                    continue
                flag = by_key.get(key)
                if flag is None or (item and key in ("ids",)):
                    raise usage(f"{where}: {key!r} is not a parameter of {command.path}.", "--input")
                std = self.key(flag)
                if std in out:
                    raise usage(f"{where}: {key!r} is given twice.", "--input")
                if flag.quantity and key == std:
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        raise usage(f"{where}: {key} is a number in {self.contract.units[flag.quantity]['unit']}.", "--input")
                    out[std] = float(value)
                elif flag.type == "list":
                    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
                        raise usage(f"{where}: {key} is a non-empty list of IDs.", "--input")
                    out[std] = list(dict.fromkeys(value))
                elif flag.type == "json" and not isinstance(value, str):
                    out[std] = json_value(flag, value, f"{where}: {key}")
                elif isinstance(value, str):
                    out[std] = self._parser_convert(flag, value)
                elif flag.type in ("integer", "number") and isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[std] = value
                elif flag.type == "boolean" and isinstance(value, bool):
                    out[std] = value
                else:
                    raise usage(f"{where}: {key} has a value of the wrong type.", "--input")
            return out

        items = None
        top = {k: v for k, v in data.items() if k != "items"}
        if "items" in data:
            raw = data["items"]
            if not isinstance(raw, list) or not raw:
                raise usage("--input items must be a non-empty list; an empty batch does nothing.", "--input")
            items = []
            seen = set()
            for n, item in enumerate(raw):
                if not isinstance(item, dict) or ("id" not in item and command.verb != "create"):
                    raise usage(f"--input item {n + 1} must be an object with an id.", "--input")
                normal = norm(item, f"--input item {n + 1}", True)
                if "id" in normal:
                    if normal["id"] in seen:
                        raise usage(f"--input names {normal['id']} twice; give each object once.", "--input")
                    seen.add(normal["id"])
                items.append(normal)
        adapter = norm(top, "--input", False)
        if items is not None:
            adapter["items"] = items
        return adapter, items

    def _parser_convert(self, flag: Any, value: str) -> Any:
        from .args import Parser

        return Parser(self.contract, {}, self.tool.name).convert(flag, value)


def json_value(flag: Any, value: Any, where: str) -> Any:
    """A structured value: an object or a list, of the type its flag's schema names (DCLI-026).
    The adapter checks the content and answers E_VALIDATION when it is wrong."""
    want = (flag.schema or {}).get("type")
    kinds = {"object": dict, "array": list}
    if not isinstance(value, (dict, list)) or (want in kinds and not isinstance(value, kinds[want])):
        raise usage(f"{where} must be a JSON {want or 'object or array'}.", flag.name)
    return value


def _file_digest(path: str, name: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise usage(f"{name} cannot read {path}: {exc.strerror}.", name) from None
    return {"sha256": digest.hexdigest(), "bytes": size}


def parallel(jobs: list[Any], fn: Any, timeout: float) -> list[Any]:
    """Run ``fn`` on each job at once and wait at most ``timeout`` seconds in total."""
    results: list[Any] = [None] * len(jobs)

    def work(i: int, job: Any) -> None:
        try:
            results[i] = ("ok", fn(job))
        except Exception as exc:  # noqa: BLE001 - reported per job
            results[i] = ("error", exc)

    threads = [threading.Thread(target=work, args=(i, j), daemon=True) for i, j in enumerate(jobs)]
    for thread in threads:
        thread.start()
    end = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    return [r if r is not None else ("timeout", None) for r in results]
