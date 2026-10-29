"""A finite stdin/stdout response-loss boundary, not an OS security sandbox.

The caller is a real, separately owned Python process. Its only request fields
are the opaque operation ID and frozen request digest. A controller callback
executes the real service once, recording its output through a separate private
channel. No service bytes are read or forwarded by this relay. After that one
dispatch returns, the relay closes the caller's pipe. Service failure remains
service failure; zero bytes at the caller does not make that outcome successful.

This module imports only the Python standard library. The controller supplies
the already-reviewed WindowsJob class and the actual service executor. It must
bind their exact sources, executable, environment, plan, and complete Job result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def file_record(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}


@dataclass(frozen=True)
class CallerSpec:
    python: str
    python_sha256: str
    probe: str
    probe_sha256: str
    operation_id: str
    request_sha256: str
    cwd: str
    environment: dict[str, str]

    def command(self):
        return [
            self.python, "-B", "-S", self.probe,
            "--operation-id", self.operation_id,
            "--request-sha256", self.request_sha256,
        ]

    def check(self):
        if os.name != "nt":
            raise RuntimeError("This controller requires the reviewed Windows Job implementation")
        if not sys.dont_write_bytecode or not sys.flags.no_site:
            raise RuntimeError("The controller must itself run with -B -S")
        if sha(self.python) != self.python_sha256 or sha(self.probe) != self.probe_sha256:
            raise ValueError("Caller executable or probe drift")
        if not Path(self.cwd).is_dir():
            raise ValueError("Caller working directory is missing")
        if len(self.request_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.request_sha256):
            raise ValueError("Invalid request digest")
        if not self.operation_id or len(self.operation_id) > 256:
            raise ValueError("A bounded opaque operation ID is required")
        allowed = {
            "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP",
            "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING", "PYTHONUNBUFFERED",
        }
        if any(key.upper() not in allowed for key in self.environment):
            raise ValueError("Caller environment has an undeclared input")
        if self.environment.get("PYTHONDONTWRITEBYTECODE") != "1":
            raise ValueError("Caller bytecode writes must be disabled")


class OwnedCaller:
    """One harmless caller, suspended until its exact owned Job is assigned.

    Only the caller's own Job may be stopped on an actual caller timeout. That
    exceptional controlled stop is recorded and never qualifies as a natural
    successful relay. This class never stops or retries the service callback.
    """

    def __init__(self, spec, output, windows_job_class):
        spec.check()
        self.spec = spec
        self.output = Path(output)
        self.output.mkdir(exist_ok=False)
        self.job = windows_job_class()
        self.process = None
        self.lines = queue.Queue()
        self.threads = []
        self.errors = []
        self.bytes_written_to_caller = 0
        self.stdin_closed_at = None
        write_new(self.output / "CALLER-INPUT.json", {
            "at": utc(), "command": spec.command(), "cwd": str(Path(spec.cwd).resolve()),
            "environment": dict(sorted(spec.environment.items())),
            "python": file_record(spec.python), "probe": file_record(spec.probe),
            "caller_receives_evidence_path": False,
            "controller_pid": os.getpid(),
        })
        try:
            self.process = subprocess.Popen(
                spec.command(), cwd=spec.cwd, env=dict(spec.environment),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=0x08000000 | 0x4, close_fds=True,
            )
            self.job.assign_and_resume(self.process)
            write_new(self.output / "CALLER-START.json", {
                "at": utc(), "root_pid": self.process.pid,
                "root_identity": self.job.root_identity,
                "initial_job": self.job.snapshot(),
            })
            self._pump(self.process.stdout, self.output / "stdout", True)
            self._pump(self.process.stderr, self.output / "stderr", False)
        except BaseException:
            # This Job contains only this explicitly owned harmless caller.
            # close() records any required controlled cleanup; no service exists yet.
            self.job.close()
            write_new(self.output / "CALLER-LAUNCH-STOP.json", {
                "at": utc(), "job_finalization": self.job.finalization,
                "service_dispatched": False,
            })
            raise

    def _pump(self, source, destination, requests):
        def run():
            try:
                with destination.open("xb") as stream:
                    while True:
                        line = source.readline()
                        if not line:
                            break
                        stream.write(line)
                        stream.flush()
                        if requests:
                            self.lines.put(line)
            except BaseException as exc:
                self.errors.append(type(exc).__name__ + ": " + str(exc))
            finally:
                source.close()
                if requests:
                    self.lines.put(None)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.threads.append(thread)

    def wait_ready(self, timeout=30):
        line = self.lines.get(timeout=timeout)
        if line is None:
            raise RuntimeError("Caller exited without its actual request")
        value = json.loads(line)
        expected = {
            "kind": "request_ready", "operation_id": self.spec.operation_id,
            "request_sha256": self.spec.request_sha256, "transport": "stdin_pipe",
        }
        if value != expected:
            raise ValueError("Caller request differs from its frozen identity")
        write_new(self.output / "CALLER-READY.json", {"at": utc(), "request": value})
        return value

    def close_response_without_delivery(self):
        if self.stdin_closed_at is not None:
            raise RuntimeError("Caller response pipe was already closed")
        # No write method is exposed by this class. The only response action is EOF.
        self.process.stdin.close()
        self.stdin_closed_at = utc()

    def finish(self, timeout=30):
        if self.stdin_closed_at is None:
            raise RuntimeError("Response pipe remains open")
        timed_out = False
        try:
            exit_code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            before = self.job.snapshot()
            expected = self.job.root_identity
            roots = [row for row in before["members"] if row["pid"] == self.process.pid]
            if not roots or any(
                row["creation_filetime"] != expected["creation_filetime"]
                or Path(row["image"]).resolve() != Path(self.spec.python).resolve()
                for row in roots
            ):
                raise RuntimeError("Caller timeout identity could not be established; retain scene")
            self.job.terminate()
            exit_code = self.process.wait(timeout=15)
        for thread in self.threads:
            thread.join(timeout=10)
        live_pumps = any(thread.is_alive() for thread in self.threads)
        deadline = time.monotonic() + 10
        while self.job.active() and time.monotonic() < deadline:
            time.sleep(0.01)
        # Any unexpected descendant is conservatively controlled, then incomplete.
        finalization = self.job.close()
        raw = (self.output / "stdout").read_bytes()
        rows = [json.loads(line) for line in raw.splitlines()]
        expected_ready = {
            "kind": "request_ready", "operation_id": self.spec.operation_id,
            "request_sha256": self.spec.request_sha256, "transport": "stdin_pipe",
        }
        expected_complete = {
            "kind": "receiver_complete", "operation_id": self.spec.operation_id,
            "request_sha256": self.spec.request_sha256, "received_bytes": 0,
            "received_sha256": EMPTY_SHA256, "eof": True,
            "service_outcome_known": False,
        }
        valid = (
            exit_code == 0 and not timed_out and not live_pumps and not self.errors
            and rows == [expected_ready, expected_complete]
            and (self.output / "stderr").read_bytes() == b""
            and finalization["natural_tree_complete"]
            and not finalization["controlled_termination"]
            and not finalization["after_cleanup"]["members"]
        )
        result = {
            "at": utc(), "exit": exit_code, "timed_out": timed_out,
            "pump_errors": self.errors, "live_pumps": live_pumps,
            "root_identity": self.job.root_identity, "job_finalization": finalization,
            "stdin_closed_at": self.stdin_closed_at,
            "bytes_written_to_caller": self.bytes_written_to_caller,
            "stdout": file_record(self.output / "stdout"),
            "stderr": file_record(self.output / "stderr"),
            "actual_receiver": rows[-1] if rows else None,
            "response_lost_at_caller": valid,
        }
        write_new(self.output / "CALLER-FINISH.json", result)
        return result


def lose_one_response(*, caller, execute_service_once, private_output, binding):
    """Invoke exactly one callback, withhold all service output, then deliver EOF.

    execute_service_once() must synchronously observe the real service through
    natural whole-Job exit and return a JSON-serializable evidence descriptor.
    Its descriptor is private controller evidence, never a caller response.
    A callback exception is recorded without retry; its original diagnostics
    remain with the callback and its outcome is not manufactured by this relay.
    """
    output = Path(private_output)
    output.mkdir(exist_ok=False)
    write_new(output / "RELAY-START.json", {
        "at": utc(), "binding": binding, "dispatch_budget": 1,
        "coverage": "service completion observed privately; caller receives EOF and zero response bytes",
        "not_covered": ["write-before-save interruption", "process-kill recovery", "power-loss durability", "OS security sandbox"],
    })
    ready = None
    service = None
    error = None
    dispatches = 0
    started_at = None
    returned_at = None
    try:
        ready = caller.wait_ready()
        started_at = utc()
        dispatches += 1
        write_new(output / "SERVICE-DISPATCH.json", {
            "at": started_at, "dispatch_index": dispatches, "request": ready,
        })
        service = execute_service_once()
        returned_at = utc()
        # JSON encoding is private evidence, never sent to caller.stdin.
        write_new(output / "SERVICE-RETURN.json", {"at": returned_at, "actual": service})
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        write_new(output / "SERVICE-ERROR.json", {"at": utc(), "actual": error, "dispatches": dispatches})
    finally:
        caller.close_response_without_delivery()
        receiver = caller.finish()
    result = {
        "at": utc(), "dispatches": dispatches,
        "service_started_at": started_at, "service_returned_at": returned_at,
        "service_callback_returned": returned_at is not None,
        "service_callback_error": error,
        "service_outcome": service,
        "caller": receiver,
        "response_lost_at_caller": receiver["response_lost_at_caller"],
        "service_success_inferred_from_empty_response": False,
        "caller_informed_of_service_outcome": False,
    }
    write_new(output / "RELAY-FINISH.json", result)
    return result
