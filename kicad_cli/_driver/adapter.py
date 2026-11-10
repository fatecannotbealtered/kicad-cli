"""The adapter interface (DCLI-260) and where each capability call goes (DCLI-262, DVER-023).

A tool's adapter subclasses ``Adapter`` and implements the capabilities its application
supports, as methods named after them. Each takes the request of
``contract/schemas/adapter.schema.json`` and returns the result of the same schema, and
reports facts only. A capability the adapter lacks gives ``E_UNSUPPORTED`` with no tool code.

Calls are routed like this:

- CLI-side capabilities run in the adapter, in the CLI process.
- Instance-side capabilities the adapter implements run in the adapter. For a tool with a
  host component, a call on a session of any other instance-side capability runs as a host
  method in that instance (DCLI-262, DCLI-274).
- With ``<PREFIX>_FAKE_HOST`` set, every call goes to the fake host instead: CLI-side calls and
  in-process instance-side calls to its address, host methods to the instances it lists under
  ``hosts/`` (DVER-023).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from typing import Any

from .hostproto import Connection, HostReplyError, NoReply, ProtocolError, Unreachable, read_discovery
from .records import pid_alive
from .state import AdapterStore

WINDOWS = sys.platform == "win32"


class AdapterError(Exception):
    """An adapter's report that the application refused or failed (DCLI-279)."""

    def __init__(self, code: str, message: str, dispatch_state: str | None = None, **extra: Any) -> None:
        """``dispatch_state`` says whether the change began. A write capability that leaves it
        out reports an unknown outcome; say ``not_started`` when nothing was sent (DCLI-275)."""
        super().__init__(message)
        self.data = {"code": code, **({"dispatch_state": dispatch_state} if dispatch_state else {}), **extra}


class Adapter:
    """Base class of a tool's adapter. ``self.store`` keeps state between calls (DCLI-260)."""

    def __init__(self, store: AdapterStore) -> None:
        self.store = store


class CapabilityError(Exception):
    """A capability call that failed with an error the application or the host reported."""

    def __init__(self, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data
        self.code = data.get("code", "E_NATIVE_FAILURE")
        # A reply that does not say whether the change began cannot count as "not started" (DCLI-275).
        state = data.get("dispatch_state")
        self.dispatch_state = state if state in ("not_started", "dispatched", "unknown") else "unknown"


class Unanswered(Exception):
    """No reply: the connection closed first, the deadline passed, or the adapter crashed.
    For a write this is outcome unknown (DCLI-063); for a read the instance is unresponsive."""

    def __init__(self, message: str, timed_out: bool = False, crashed: bool = False, sent: bool = True) -> None:
        super().__init__(message)
        self.timed_out = timed_out
        self.crashed = crashed
        self.sent = sent  # False when the request never left the CLI


class BadReply(Unanswered):
    """The request was sent, but its reply cannot be understood: for a write the outcome is
    unknown, for a read it is a protocol error."""


class BadProtocol(Exception):
    """The host speaks another major protocol version, or broke the handshake (DCLI-273);
    nothing was sent."""


class Lost(Exception):
    """A live host whose address cannot be reached: the session is lost (DCLI-278)."""


class Backend:
    def __init__(self, adapter: Adapter, contract_caps: dict[str, dict[str, Any]], *, host_component: bool,
                 fake_address: str | None, hosts_dir: str, cli_version: str, protocol_version: str) -> None:
        self.adapter = adapter
        self.caps = contract_caps
        self.host_component = host_component
        self.fake = fake_address
        self.hosts_dir = hosts_dir
        self.cli_version = cli_version
        self.protocol_version = protocol_version
        self._main: Connection | None = None
        self._hosts: dict[str, Connection] = {}
        # Called by the watchdog when an in-process call passes its deadline; it reports the
        # call and ends the process, which cannot wait for the application any longer.
        self.on_hang: Any = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ availability

    def adapter_has(self, cap: str) -> bool:
        method = getattr(type(self.adapter), cap, None)
        return callable(method) and getattr(Adapter, cap, None) is None

    def on_host(self, cap: str, session: str | None) -> bool:
        """An instance-side capability goes to the host component unless the adapter in the CLI
        process implements it itself; the two can be mixed capability by capability (DCLI-262)."""
        return (self.host_component and session is not None and self.caps[cap]["side"] == "instance"
                and (self.fake is not None or not self.adapter_has(cap)))

    def has(self, cap: str, session: str | None = None) -> bool:
        if self.on_host(cap, session):
            try:
                conn = self._host(session)
            except (Lost, Unanswered, BadProtocol):
                return True  # the call itself reports what is wrong
            return conn is None or cap in (conn.hello or {}).get("methods", [])
        if self.fake:
            return cap in self.caps  # the fake host answers every capability in place of the adapter (DVER-020)
        return self.adapter_has(cap)

    # ------------------------------------------------------------------ calls

    def call(self, cap: str, request: dict[str, Any], deadline_ms: int) -> Any:
        session = request.get("session")
        if self.on_host(cap, session):
            conn = self._host(session)
            if conn is None:
                raise Lost(f"no host component of session {session} is running")
            return self._rpc(conn, cap, request, deadline_ms, host=session)
        if self.fake:
            return self._rpc(self._fake_main(deadline_ms), cap, request, deadline_ms, host=None)
        return self._in_process(cap, request, deadline_ms)

    def _in_process(self, cap: str, request: dict[str, Any], deadline_ms: int) -> Any:
        """Run an adapter method on the main thread, where COM and most application APIs
        expect it. A watchdog ends the call when the deadline passes: a call that hangs is a
        call with no reply, as over the host protocol (DCLI-278)."""
        method = getattr(self.adapter, cap)
        fired, reported = threading.Event(), threading.Event()

        def watchdog() -> None:
            fired.set()
            try:
                self.on_hang(cap, deadline_ms)  # reports the call, then ends the process
            finally:
                reported.set()

        timer = threading.Timer(deadline_ms / 1000, watchdog) if self.on_hang else None
        if timer is not None:
            timer.daemon = True
            timer.start()
        try:
            return method(dict(request))
        except AdapterError as exc:
            raise CapabilityError(str(exc), exc.data) from None
        except Exception as exc:  # noqa: BLE001 - an adapter fault must not look like a known outcome
            raise Unanswered(f"the adapter failed in {cap}: {type(exc).__name__}: {exc}", crashed=True) from None
        finally:
            if timer is not None:
                timer.cancel()
            if fired.is_set():
                # The watchdog reports this call and ends the process; the call must not go on meanwhile.
                reported.wait()

    def _rpc(self, conn: Connection, cap: str, request: dict[str, Any], deadline_ms: int, host: str | None) -> Any:
        try:
            return conn.call(cap, request, deadline_ms)
        except HostReplyError as exc:
            raise CapabilityError(str(exc), exc.data) from None
        except NoReply as exc:
            self._forget(host)
            raise Unanswered(str(exc), exc.timed_out) from None
        except ProtocolError as exc:
            self._forget(host)
            raise BadReply(f"the reply to {cap} cannot be understood: {exc}") from None

    def _forget(self, host: str | None) -> None:
        with self._lock:
            if host is None:
                self._main = None
            else:
                self._hosts.pop(host, None)

    def _fake_main(self, deadline_ms: int) -> Connection:
        with self._lock:
            if self._main is not None and self._main.stream is not None and not self._main.stream.closed:
                return self._main
        conn = Connection(self.fake or "", self.cli_version, self.protocol_version)
        try:
            conn.open(min(deadline_ms, 5000))
        except Unreachable as exc:
            raise Unanswered(f"the fake host does not answer: {exc}", sent=False) from None
        except NoReply as exc:
            raise Unanswered(str(exc), exc.timed_out, sent=False) from None
        except ProtocolError as exc:
            raise BadProtocol(str(exc)) from None
        with self._lock:
            self._main = conn
        return conn

    # ------------------------------------------------------------------ host components

    def probe_hosts(self, deadline_ms: int = 2000) -> dict[str, dict[str, Any]]:
        return self.start_probe(deadline_ms)()

    def start_probe(self, deadline_ms: int = 2000) -> Any:
        """Connect to every live host in ``hosts/`` at once, each within ``deadline_ms`` for
        connecting and the hello together (DCLI-205), and return at once. The function returned
        waits for the probes until that deadline and gives session ID -> ``{status, hello,
        address}``, where status is ``ready``, ``unresponsive``, ``lost`` or ``protocol``; a
        probe still running then is ``unresponsive``."""
        entries = [e for e in read_discovery(self.hosts_dir) if pid_alive(e["pid"])]
        results: list[tuple[dict[str, Any], Any]] = [(e, "unresponsive") for e in entries]

        def probe(i: int, entry: dict[str, Any]) -> None:
            conn = Connection(entry["address"], self.cli_version, self.protocol_version)
            try:
                conn.open(deadline_ms)
                results[i] = (entry, conn)
            except Unreachable:
                results[i] = (entry, "lost")
            except NoReply as exc:
                results[i] = (entry, "unresponsive" if exc.timed_out else "lost")
            except ProtocolError as exc:
                results[i] = (entry, BadProtocol(str(exc)))

        threads = [threading.Thread(target=probe, args=(i, e), daemon=True) for i, e in enumerate(entries)]
        for thread in threads:
            thread.start()
        end = time.monotonic() + deadline_ms / 1000 + 0.2

        def finish() -> dict[str, dict[str, Any]]:
            for thread in threads:
                thread.join(max(0.0, end - time.monotonic()))
            return self._probed(list(results))

        return finish

    def _probed(self, results: list[tuple[dict[str, Any], Any]]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for entry, outcome in results:
            name = entry["_file"][:-5]
            if isinstance(outcome, Connection):
                session = (outcome.hello or {}).get("session", {}).get("id", name)
                with self._lock:
                    old = self._hosts.get(session)
                    self._hosts[session] = outcome
                if old is not None:
                    old.close()
                found[session] = {"status": "ready", "hello": outcome.hello, "address": entry["address"]}
            elif isinstance(outcome, BadProtocol):
                found[name] = {"status": "protocol", "error": str(outcome), "address": entry["address"]}
            else:
                found[name] = {"status": outcome or "unresponsive", "address": entry["address"]}
        return found

    def _host(self, session: str | None) -> Connection | None:
        assert session is not None
        with self._lock:
            conn = self._hosts.get(session)
            if conn is not None and conn.stream is not None and not conn.stream.closed:
                return conn
        found = self.probe_hosts()
        info = found.get(session)
        if info is None:
            return None
        if info["status"] == "protocol":
            raise BadProtocol(info["error"])
        if info["status"] == "lost":
            raise Lost(f"the host component of session {session} does not accept connections")
        if info["status"] == "unresponsive":
            raise Unanswered(f"the host component of session {session} did not answer hello", timed_out=True, sent=False)
        with self._lock:
            return self._hosts.get(session)

    def close(self) -> None:
        with self._lock:
            conns = ([self._main] if self._main else []) + list(self._hosts.values())
            self._main = None
            self._hosts.clear()
        for conn in conns:
            conn.close()


def session_id(prefix: str, pid: int) -> str:
    """A session ID from the process ID and its start time, stable for the life of the
    process and never reused by a later one (DCLI-203). Adapters call this in ``sessions``."""
    return f"{prefix}-{pid}-{start_time(pid):x}"


def start_time(pid: int) -> int:
    """When a process started, in seconds, or 0 when it cannot be told."""
    try:
        if WINDOWS:
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return 0
            try:
                times = [wintypes.FILETIME() for _ in range(4)]
                kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
                if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                    return 0
                return (times[0].dwHighDateTime << 32 | times[0].dwLowDateTime) // 10_000_000
            finally:
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle(handle)
        if os.path.exists(f"/proc/{pid}/stat"):
            with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
                return int(handle.read().rsplit(")", 1)[1].split()[19])
        out = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=5)
        return int(hashlib.sha256(out.stdout.strip().encode()).hexdigest()[:10], 16)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0
