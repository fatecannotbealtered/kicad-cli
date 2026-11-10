"""The CLI side of the host protocol (DCLI-270 to DCLI-280).

A local connection (a named pipe on Windows, a Unix socket elsewhere), Content-Length
framing with CRLF, JSON-RPC 2.0, ``hello`` first, and ``deadline_ms`` on every request.
Each CLI call opens its own connections and closes them when it ends (DCLI-278). A reply
that does not arrive in time is ``NoReply``, never a failure the CLI guesses at.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import socket
import sys
import time
from typing import Any, Callable

WINDOWS = sys.platform == "win32"
HEADER = re.compile(rb"Content-Length: ([0-9]{1,9})\r\n\r\n")
MAX_HEADER = 32
MAX_BODY = 64 * 1024 * 1024


class Unreachable(Exception):
    """Nothing answers at the address."""


class NoReply(Exception):
    """The connection closed before the reply, or the deadline passed (DCLI-278)."""

    def __init__(self, message: str, timed_out: bool) -> None:
        super().__init__(message)
        self.timed_out = timed_out


class ProtocolError(Exception):
    """The peer broke the protocol, or speaks another major version (DCLI-273)."""


class HostReplyError(Exception):
    """The host replied with an error (DCLI-279). ``data`` holds ``code`` and ``dispatch_state``."""

    def __init__(self, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data
        self.code = data.get("code")


class _Stream:
    def __init__(self, recv: Callable[[int], bytes], send: Callable[[bytes], None], close: Callable[[], None],
                 settimeout: Callable[[float | None], None]) -> None:
        self.recv, self.send, self._close, self.settimeout = recv, send, close, settimeout
        self.buffer = b""
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._close()

    def _chunk(self, deadline: float | None) -> bytes:
        """The next bytes, within what is left of the deadline of the whole reply."""
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            self.settimeout(left)
        return self.recv(65536)

    def read(self, deadline: float | None = None) -> Any:
        while True:
            end = self.buffer.find(b"\r\n\r\n")
            if end != -1:
                break
            if len(self.buffer) > MAX_HEADER:
                raise ProtocolError("the header of a reply is too long")
            chunk = self._chunk(deadline)
            if not chunk:
                return None
            self.buffer += chunk
        match = HEADER.fullmatch(self.buffer[: end + 4])
        if not match or int(match.group(1)) > MAX_BODY:
            raise ProtocolError("a reply has a bad Content-Length header")
        size = int(match.group(1))
        self.buffer = self.buffer[end + 4:]
        while len(self.buffer) < size:
            chunk = self._chunk(deadline)
            if not chunk:
                return None
            self.buffer += chunk
        body, self.buffer = self.buffer[:size], self.buffer[size:]
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProtocolError(f"a reply is not UTF-8 JSON: {exc}") from None

    def write(self, message: Any) -> None:
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send(b"Content-Length: %d\r\n\r\n" % len(body) + body)


def _connect(address: str, timeout: float) -> _Stream:
    return _pipe_connect(address, timeout) if WINDOWS else _unix_connect(address, timeout)


def _unix_connect(address: str, timeout: float) -> _Stream:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(address)
    except OSError as exc:
        sock.close()
        raise Unreachable(f"cannot connect to {address}: {exc}") from None

    def recv(n: int) -> bytes:
        try:
            return sock.recv(n)
        except socket.timeout:
            raise TimeoutError() from None
        except (ConnectionResetError, BrokenPipeError):
            return b""

    def close() -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    return _Stream(recv, sock.sendall, close, sock.settimeout)


if WINDOWS:
    import _winapi

    _GONE = {2, 109, 232, 233}


def _pipe_connect(address: str, timeout: float) -> _Stream:
    deadline = time.monotonic() + timeout
    while True:
        try:
            _winapi.WaitNamedPipe(address, max(1, int(timeout * 1000)))
            handle = _winapi.CreateFile(address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
                                        _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, _winapi.NULL)
            break
        except OSError as exc:
            busy = exc.winerror in (_winapi.ERROR_SEM_TIMEOUT, _winapi.ERROR_PIPE_BUSY)
            if not busy or time.monotonic() >= deadline:
                raise Unreachable(f"cannot connect to {address}: {exc}") from None
            time.sleep(0.01)
    wait_ms = {"value": _winapi.INFINITE}

    def recv(n: int) -> bytes:
        try:
            ov, err = _winapi.ReadFile(handle, n, overlapped=True)
            if err == _winapi.ERROR_IO_PENDING:
                if _winapi.WaitForMultipleObjects([ov.event], False, wait_ms["value"]) == _winapi.WAIT_TIMEOUT:
                    ov.cancel()
                    try:
                        ov.GetOverlappedResult(True)
                    except OSError:
                        pass
                    raise TimeoutError()
            _, err = ov.GetOverlappedResult(True)
        except OSError as exc:
            if getattr(exc, "winerror", None) in _GONE:
                return b""
            raise
        if err in _GONE:
            return b""
        return bytes(ov.getbuffer())

    def send(data: bytes) -> None:
        try:
            ov, err = _winapi.WriteFile(handle, data, overlapped=True)
            if err == _winapi.ERROR_IO_PENDING:
                if _winapi.WaitForMultipleObjects([ov.event], False, wait_ms["value"]) == _winapi.WAIT_TIMEOUT:
                    ov.cancel()
                    try:
                        ov.GetOverlappedResult(True)
                    except OSError:
                        pass
                    raise TimeoutError()
            _, err = ov.GetOverlappedResult(True)
        except OSError as exc:
            raise BrokenPipeError(str(exc)) from None
        if err:
            raise BrokenPipeError(f"write failed with error {err}")

    def close() -> None:
        try:
            _winapi.CloseHandle(handle)
        except OSError:
            pass

    def settimeout(seconds: float | None) -> None:
        wait_ms["value"] = _winapi.INFINITE if seconds is None else max(1, int(seconds * 1000))

    return _Stream(recv, send, close, settimeout)


class Connection:
    """One connection to a host, opened with ``hello`` (DCLI-273)."""

    def __init__(self, address: str, cli_version: str, protocol_version: str) -> None:
        self.address = address
        self.cli_version = cli_version
        self.protocol_version = protocol_version
        self.stream: _Stream | None = None
        self.hello: dict[str, Any] | None = None
        self._ids = itertools.count(1)

    def open(self, deadline_ms: int) -> dict[str, Any]:
        """Connect and say hello, both within ``deadline_ms`` (DCLI-205)."""
        end = time.monotonic() + deadline_ms / 1000
        self.stream = _connect(self.address, min(5.0, deadline_ms / 1000))
        left = max(1, int((end - time.monotonic()) * 1000))
        hello = self.call("hello", {"protocol_version": self.protocol_version, "cli_version": self.cli_version}, left)
        if not isinstance(hello, dict) or not isinstance(hello.get("protocol_version"), str):
            self.close()
            raise ProtocolError("the host's hello reply has no protocol_version")
        if hello["protocol_version"].split(".")[0] != self.protocol_version.split(".")[0]:
            self.close()
            raise ProtocolError(
                f"the host speaks protocol {hello['protocol_version']} and this CLI speaks {self.protocol_version}"
            )
        self.hello = hello
        return hello

    def call(self, method: str, params: dict[str, Any], deadline_ms: int) -> Any:
        if self.stream is None or self.stream.closed:
            raise NoReply("the connection is closed", timed_out=False)
        rpc_id = next(self._ids)
        message = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": {**params, "deadline_ms": deadline_ms}}
        self.stream.settimeout(deadline_ms / 1000)
        try:
            self.stream.write(message)
            reply = self.stream.read(time.monotonic() + deadline_ms / 1000)
        except TimeoutError:
            self.close()  # a late reply must not answer the next request
            raise NoReply(f"no reply to {method} within {deadline_ms} ms", timed_out=True) from None
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            self.close()
            raise NoReply(f"the connection broke during {method}: {exc}", timed_out=False) from None
        except ProtocolError:
            self.close()
            raise
        if reply is None:
            self.close()
            raise NoReply(f"the host closed the connection before it replied to {method}", timed_out=False)
        if not isinstance(reply, dict) or reply.get("id") != rpc_id or reply.get("jsonrpc") != "2.0":
            self.close()
            raise ProtocolError(f"the reply to {method} is not a JSON-RPC 2.0 reply to request {rpc_id}")
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            data = error.get("data") if isinstance(error.get("data"), dict) else {}
            if error.get("code") != -32000 or not isinstance(data.get("code"), str):
                raise ProtocolError(f"the host answered {method} with a malformed error: {str(error)[:200]}")
            raise HostReplyError(str(error.get("message") or data["code"]), data)
        if "result" not in reply:
            raise ProtocolError(f"the reply to {method} has neither result nor error")
        return reply["result"]

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()


def read_discovery(folder: str) -> list[dict[str, Any]]:
    """The discovery files under ``hosts/`` (DCLI-271), each with its file name."""
    out = []
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get("address"), str) and isinstance(data.get("pid"), int):
                data["_file"] = name
                out.append(data)
    return out
