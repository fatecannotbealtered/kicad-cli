"""One owned, bounded official-consumer call with durable private evidence.

This is a process boundary, not a DRC checker or debugger. Exit zero only
permits the caller to validate the fresh output. Windows owns a new Job before
resuming the root; POSIX owns a new process group, whose members may escape by
creating another session. No existing Job or unrelated process is terminated.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import time
from ctypes import wintypes as W
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class _EvidenceWriteError(OSError):
    pass


def _put(path, value, *, initial=False):
    """Flush before exposing a complete record; failure never means success."""
    path = Path(path)
    temporary = path.with_name(path.name + ".writing")
    try:
        with temporary.open("xb") as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if initial and path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    except OSError as exc:
        raise _EvidenceWriteError("Private evidence could not be persisted") from exc


class _WindowsJob:
    """A new unnamed Job, derived from the existing finite bench owner."""

    def __init__(self):
        self.handle = None
        self.control_events = []
        self.root_identity = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        k.CreateJobObjectW.restype = W.HANDLE
        k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        k.SetInformationJobObject.restype = W.BOOL
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        k.AssignProcessToJobObject.restype = W.BOOL
        k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
        k.TerminateJobObject.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]
        k.CloseHandle.restype = W.BOOL
        k.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        k.OpenThread.restype = W.HANDLE
        k.ResumeThread.argtypes = [W.HANDLE]
        k.ResumeThread.restype = W.DWORD
        k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        k.CreateToolhelp32Snapshot.restype = W.HANDLE

        class Limits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", W.DWORD),
                ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t),
                ("process_limit", W.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", W.DWORD),
                ("scheduling", W.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Limits),
                ("io", ctypes.c_uint64 * 6),
                ("process_mem", ctypes.c_size_t),
                ("job_mem", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; never BREAKAWAY_OK.
        if not k.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            k.CloseHandle(self.handle)
            self.handle = None
            raise ctypes.WinError(error)

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        initial = self.snapshot()
        if len(initial["members"]) != 1 or initial["members"][0]["pid"] != process.pid:
            raise RuntimeError("New owned Job must contain exactly its suspended root")
        self.root_identity = dict(initial["members"][0], assigned_before_resume=True)
        return self.root_identity

    def resume(self, process):
        k = self.kernel

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("size", W.DWORD),
                ("usage", W.DWORD),
                ("tid", W.DWORD),
                ("pid", W.DWORD),
                ("base_priority", W.LONG),
                ("delta", W.LONG),
                ("flags", W.DWORD),
            ]

        k.Thread32First.argtypes = [W.HANDLE, ctypes.POINTER(ThreadEntry)]
        k.Thread32Next.argtypes = [W.HANDLE, ctypes.POINTER(ThreadEntry)]
        snapshot = k.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        ids = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = k.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.pid == process.pid:
                    ids.append(entry.tid)
                found = k.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            k.CloseHandle(snapshot)
        if len(ids) != 1:
            raise RuntimeError("Suspended root must have exactly one initial thread")
        thread = k.OpenThread(2, False, ids[0])
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if k.ResumeThread(thread) != 1:
                raise RuntimeError("Unexpected initial thread suspend count")
        finally:
            k.CloseHandle(thread)

    def snapshot(self):
        k = self.kernel
        k.QueryInformationJobObject.argtypes = [
            W.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            W.DWORD,
            ctypes.c_void_p,
        ]
        k.QueryInformationJobObject.restype = W.BOOL
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        k.OpenProcess.restype = W.HANDLE
        k.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)]
        k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.c_void_p] * 4
        k.QueryFullProcessImageNameW.argtypes = [
            W.HANDLE,
            W.DWORD,
            W.LPWSTR,
            ctypes.POINTER(W.DWORD),
        ]
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]

        def checked(value):
            if not value:
                raise ctypes.WinError(ctypes.get_last_error())
            return value

        def ids():
            for capacity in (16, 64, 256, 1024, 4096):

                class Members(ctypes.Structure):
                    _fields_ = [
                        ("assigned", W.DWORD),
                        ("count", W.DWORD),
                        ("ids", ctypes.c_size_t * capacity),
                    ]

                value = Members()
                if k.QueryInformationJobObject(
                    self.handle, 3, ctypes.byref(value), ctypes.sizeof(value), None
                ):
                    if value.assigned == value.count and value.count <= capacity:
                        return list(value.ids[: value.count])
                elif ctypes.get_last_error() != 234:
                    raise ctypes.WinError(ctypes.get_last_error())
            raise RuntimeError("Owned Job exceeds bounded membership capacity")

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current, members = ids(), []
            try:
                for pid in current:
                    handle = checked(k.OpenProcess(0x00100000 | 0x1000, False, pid))
                    try:
                        member = W.BOOL()
                        checked(k.IsProcessInJob(handle, self.handle, ctypes.byref(member)))
                        if not member.value or k.WaitForSingleObject(handle, 0) != 258:
                            raise RuntimeError("Process lifetime/membership changed")
                        times = [ctypes.c_ulonglong() for _ in range(4)]
                        checked(k.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]))
                        size = W.DWORD(32768)
                        image = ctypes.create_unicode_buffer(size.value)
                        checked(k.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)))
                        members.append(
                            {
                                "pid": pid,
                                "creation_filetime": times[0].value,
                                "image": image.value,
                                "image_sha256": _digest(image.value),
                                "owned_job_member": True,
                            }
                        )
                    finally:
                        k.CloseHandle(handle)
            except (OSError, RuntimeError):
                time.sleep(0.001)
                continue
            if ids() == current:
                return {"at": _now(), "members": members, "all_members_attributed": True}
        raise RuntimeError("Could not attribute a stable owned Job member set")

    def active(self):
        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_int64 * 4),
                ("page_faults", W.DWORD),
                ("total", W.DWORD),
                ("active", W.DWORD),
                ("terminated", W.DWORD),
            ]

        value = Accounting()
        k = self.kernel
        k.QueryInformationJobObject.argtypes = [
            W.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            W.DWORD,
            ctypes.c_void_p,
        ]
        if not k.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.active

    def terminate(self):
        before = self.snapshot()
        self.control_events.append({"at": _now(), "action": "terminate_owned_job", **before})
        if not self.kernel.TerminateJobObject(self.handle, 0xE0000001):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        before = self.snapshot()
        if before["members"]:
            self.terminate()
        deadline = time.monotonic() + 10
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.01)
        after = self.snapshot()
        if after["members"]:
            raise RuntimeError("Owned Job still has members; completion unproven")
        if not self.kernel.CloseHandle(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = None
        return {
            "kind": "windows_job",
            "natural_tree_complete": not self.control_events,
            "controlled_termination": bool(self.control_events),
            "before_close": before,
            "after_cleanup": after,
            "control_events": self.control_events,
            "limitation": "Job completion only; no debug-event or descendant exit-code census",
        }


def _linux_identity(pid):
    directory = Path("/proc") / str(pid)
    fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
    image = str((directory / "exe").resolve(strict=True))
    return {
        "pid": pid,
        "birth_identity": {
            "linux_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "start_ticks": int(fields[19]),
        },
        "image": image,
        "image_sha256": _digest(image),
        "process_group": int(fields[2]),
        "session": int(fields[3]),
        "ownership": "new_session_process_group",
    }


class _PosixGroup:
    """Linux /proc observation, explicitly limited to the original group."""

    def __init__(self, process):
        self.pid = process.pid
        self.control_events = []
        self.root_identity = _linux_identity(process.pid)
        if (
            self.root_identity["process_group"] != self.pid
            or self.root_identity["session"] != self.pid
        ):
            raise RuntimeError("Expected newly owned POSIX session/process group")

    def snapshot(self):
        members = []
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == self.pid and fields[0] != "Z":
                    members.append(_linux_identity(int(path.name)))
            except FileNotFoundError:
                continue
        return {"at": _now(), "members": members, "all_members_attributed": True}

    def active(self):
        return len(self.snapshot()["members"])

    def terminate(self):
        before = self.snapshot()
        self.control_events.append({"at": _now(), "action": "kill_owned_group", **before})
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def close(self):
        before = self.snapshot()
        if before["members"]:
            self.terminate()
        deadline = time.monotonic() + 10
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.01)
        after = self.snapshot()
        if after["members"]:
            raise RuntimeError("Owned POSIX process group remains active")
        return {
            "kind": "posix_process_group",
            "natural_tree_complete": not self.control_events,
            "controlled_termination": bool(self.control_events),
            "before_close": before,
            "after_cleanup": after,
            "control_events": self.control_events,
            "limitation": "Only original process group; descendants may escape via a new session",
        }


def _execution(exit_code, platform):
    if platform == "nt" and (exit_code & 0xFFFFFFFF) >= 0xC0000000:
        return "native_fatal_exit"
    if platform != "nt" and exit_code < 0:
        return "native_fatal_exit"
    return "completed_for_output_validation" if exit_code == 0 else "native_nonzero"


def _natural_completion(finalization):
    return (
        isinstance(finalization, dict)
        and finalization.get("kind") in {"windows_job", "posix_process_group"}
        and finalization.get("natural_tree_complete") is True
        and finalization.get("controlled_termination") is False
        and finalization.get("control_events") == []
        and all(
            isinstance(finalization.get(name), dict)
            and finalization[name].get("members") == []
            and finalization[name].get("all_members_attributed") is True
            for name in ("before_close", "after_cleanup")
        )
    )


def run(command: list[str], *, cwd: Path, env: dict, output: Path, timeout: float) -> dict:
    """Run once; return persisted process facts, never an engineering approval.

    ``output`` must not exist. command.json/stdout/stderr/record.json are private
    evidence; command arguments may contain local paths. Environment values are
    represented only by hashes. A failed evidence write raises to the caller.
    No timeout or failed invocation is automatically replayed.
    """
    if not command or any(not isinstance(item, str) or "\0" in item for item in command):
        raise ValueError("A nonempty string command without NUL is required")
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("Explicit string environment is required")
    env = dict(env)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    executable = Path(command[0]).expanduser()
    if not executable.is_absolute():
        raise ValueError("Executable must be an explicit absolute path")
    executable = executable.resolve(strict=True)
    if not stat.S_ISREG(executable.stat().st_mode):
        raise ValueError("Executable must be a regular file")
    cwd = Path(cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("Working directory must be an existing directory")
    command = [str(executable), *command[1:]]
    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    started = time.monotonic()
    record = {
        "schema_version": 1,
        "platform": os.name,
        "private_evidence": True,
        "execution": "not_started",
        "started_at": _now(),
        "finished_at": None,
        "timeout_seconds": timeout,
        "command_sha256": hashlib.sha256(_canonical(command)).hexdigest(),
        "cwd": str(cwd),
        "cwd_sha256": hashlib.sha256(str(cwd).encode()).hexdigest(),
        "environment_sha256": hashlib.sha256(_canonical(env)).hexdigest(),
        "environment_keys": sorted(env),
        "executable": {"path": str(executable), "sha256": _digest(executable)},
        "root_identity": None,
        "exit": None,
        "job_finalization": None,
        "timed_out": False,
        "output_validation_permitted": False,
        "interpretation": (
            "Process completion does not establish valid output or absence of DRC findings"
        ),
    }
    _put(output / "command.json", {"command": command, "cwd": str(cwd)}, initial=True)
    _put(output / "record.json", record, initial=True)
    process, owner, raised = None, None, None
    try:
        if os.name == "nt":
            owner = _WindowsJob()
        elif not Path("/proc/sys/kernel/random/boot_id").is_file():
            raise RuntimeError("POSIX birth observation requires Linux /proc; unsupported here")
        with (output / "stdout").open("xb") as stdout, (output / "stderr").open("xb") as stderr:
            options = (
                {"creationflags": 0x08000000 | 0x4}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, **options
            )
            record.update(execution="started", root_pid=process.pid)
            # Persist the birth stage even if subsequent attribution fails.
            _put(output / "record.json", record)
            if os.name == "nt":
                identity = owner.assign(process)
            else:
                owner = _PosixGroup(process)
                identity = owner.root_identity
            record["root_identity"] = identity
            if (
                Path(identity["image"]).resolve() != executable
                or identity["image_sha256"] != record["executable"]["sha256"]
            ):
                raise RuntimeError("Actual root image differs from the selected executable")
            _put(output / "record.json", record)
            if os.name == "nt":
                owner.resume(process)
            record["resumed_at"] = _now()
            _put(output / "record.json", record)
            while process.poll() is None or owner.active():
                if time.monotonic() - started >= timeout:
                    record.update(execution="timeout", timed_out=True)
                    _put(output / "record.json", record)
                    owner.terminate()
                    break
                time.sleep(0.01)
            process.wait(timeout=10)
            record["exit"] = process.returncode
            record["job_finalization"] = owner.close()
            owner = None
            if not record["timed_out"]:
                record["execution"] = _execution(process.returncode, os.name)
    except BaseException as exc:
        raised = (
            exc if isinstance(exc, _EvidenceWriteError) or not isinstance(exc, Exception) else None
        )
        record["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        record["execution"] = "observation_incomplete" if process is not None else "launch_failed"
        # A still-suspended root may not have joined the Job. Killing this exact
        # Popen handle is bounded to the child created by this invocation.
        if process is not None and process.poll() is None:
            if owner is not None and owner.root_identity is not None:
                owner.terminate()
            else:
                process.kill()
                record["unassigned_root_stopped"] = True
            process.wait(timeout=10)
        if process is not None:
            record["exit"] = process.returncode
    finally:
        if owner is not None:
            try:
                record["job_finalization"] = owner.close()
            except Exception as exc:
                record["execution"] = "observation_incomplete"
                record["finalization_error"] = str(exc)[:500]
        record["finished_at"] = _now()
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        for name in ("stdout", "stderr"):
            path = output / name
            if path.is_file():
                record[name + "_sha256"] = _digest(path)
                record[name + "_bytes"] = path.stat().st_size
        try:
            current_image = _digest(executable)
        except OSError:
            current_image = None
        if current_image != record["executable"]["sha256"]:
            record.update(execution="observation_incomplete", executable_changed=True)
        finalization = record.get("job_finalization") or {}
        if record["execution"] in {"completed_for_output_validation", "native_nonzero"} and not (
            _natural_completion(finalization)
        ):
            record["execution"] = "observation_incomplete"
        record["output_validation_permitted"] = record["execution"] in {
            "completed_for_output_validation",
            "native_nonzero",
        } and _natural_completion(finalization)
        _put(output / "record.json", record)
    if raised is not None:
        raise raised
    return record
