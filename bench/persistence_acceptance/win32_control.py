"""Own a hidden child tree, freeze its attributed writer, and stop only that job.

External Windows control only. No product hook, patch, memory write or SDK import.
"""

import ctypes as C
import datetime
import msvcrt
import os
import subprocess
import time
from ctypes import wintypes as W
from pathlib import Path

K = C.WinDLL("kernel32", use_last_error=True)
PTR = C.c_void_p
SIZE = C.c_size_t


class Startup(C.Structure):
    _fields_ = [
        ("cb", W.DWORD),
        ("reserved", W.LPWSTR),
        ("desktop", W.LPWSTR),
        ("title", W.LPWSTR),
        ("x", W.DWORD),
        ("y", W.DWORD),
        ("xsize", W.DWORD),
        ("ysize", W.DWORD),
        ("xchars", W.DWORD),
        ("ychars", W.DWORD),
        ("fill", W.DWORD),
        ("flags", W.DWORD),
        ("show", W.WORD),
        ("reserved_size", W.WORD),
        ("reserved_bytes", PTR),
        ("stdin", W.HANDLE),
        ("stdout", W.HANDLE),
        ("stderr", W.HANDLE),
    ]


class ProcessInfo(C.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


class BasicLimits(C.Structure):
    _fields_ = [
        ("process_time", C.c_longlong),
        ("job_time", C.c_longlong),
        ("flags", W.DWORD),
        ("min_working", SIZE),
        ("max_working", SIZE),
        ("active_limit", W.DWORD),
        ("affinity", SIZE),
        ("priority", W.DWORD),
        ("scheduling", W.DWORD),
    ]


class Limits(C.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", C.c_ulonglong * 6),
        ("process_memory", SIZE),
        ("job_memory", SIZE),
        ("peak_process", SIZE),
        ("peak_job", SIZE),
    ]


class ExceptionRecord(C.Structure):
    _fields_ = [
        ("code", W.DWORD),
        ("flags", W.DWORD),
        ("record", PTR),
        ("address", PTR),
        ("count", W.DWORD),
        ("information", SIZE * 15),
    ]


class ExceptionInfo(C.Structure):
    _fields_ = [("record", ExceptionRecord), ("first_chance", W.DWORD)]


class CreateDebugInfo(C.Structure):
    _fields_ = [
        ("file", W.HANDLE),
        ("process", W.HANDLE),
        ("thread", W.HANDLE),
        ("base", PTR),
        ("offset", W.DWORD),
        ("size", W.DWORD),
        ("local_base", PTR),
        ("start", PTR),
        ("image_name", PTR),
        ("unicode", W.WORD),
    ]


class DebugUnion(C.Union):
    _fields_ = [("exception", ExceptionInfo), ("created", CreateDebugInfo), ("file", W.HANDLE)]


class DebugEvent(C.Structure):
    _fields_ = [("code", W.DWORD), ("pid", W.DWORD), ("tid", W.DWORD), ("info", DebugUnion)]


def bind(name, args, result=W.BOOL):
    f = getattr(K, name)
    f.argtypes = args
    f.restype = result
    return f


create_job = bind("CreateJobObjectW", [PTR, W.LPCWSTR], W.HANDLE)
set_job = bind("SetInformationJobObject", [W.HANDLE, C.c_int, PTR, W.DWORD])
assign_job = bind("AssignProcessToJobObject", [W.HANDLE, W.HANDLE])
in_job = bind("IsProcessInJob", [W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)])
terminate_job = bind("TerminateJobObject", [W.HANDLE, W.UINT])
create_process = bind(
    "CreateProcessW",
    [
        W.LPCWSTR,
        W.LPWSTR,
        PTR,
        PTR,
        W.BOOL,
        W.DWORD,
        PTR,
        W.LPCWSTR,
        C.POINTER(Startup),
        C.POINTER(ProcessInfo),
    ],
)
resume_thread = bind("ResumeThread", [W.HANDLE], W.DWORD)
wait_handle = bind("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD)
get_exit = bind("GetExitCodeProcess", [W.HANDLE, C.POINTER(W.DWORD)])
get_times = bind("GetProcessTimes", [W.HANDLE, PTR, PTR, PTR, PTR])
terminate_process = bind("TerminateProcess", [W.HANDLE, W.UINT])
close = bind("CloseHandle", [W.HANDLE])
open_process = bind("OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
image_name = bind("QueryFullProcessImageNameW", [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)])
debug_attach = bind("DebugActiveProcess", [W.DWORD])
debug_stop = bind("DebugActiveProcessStop", [W.DWORD])
debug_kill_on_exit = bind("DebugSetProcessKillOnExit", [W.BOOL])
debug_wait = bind("WaitForDebugEvent", [C.POINTER(DebugEvent), W.DWORD])
debug_continue = bind("ContinueDebugEvent", [W.DWORD, W.DWORD, W.DWORD])
peek_pipe = bind("PeekNamedPipe", [W.HANDLE, PTR, W.DWORD, PTR, C.POINTER(W.DWORD), PTR])
query_job = bind("QueryInformationJobObject", [W.HANDLE, C.c_int, PTR, W.DWORD, PTR])


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


def inspect_job(job_handle):
    """Read membership of a caller-owned Job without taking its lifetime ownership."""
    view = object.__new__(OwnedProcess)
    view.job = job_handle
    return view.tree_snapshot()


class OwnedProcess:
    def __init__(
        self, argv, cwd, env, stdout_path, stderr_path, *, pipe=False, allow_control=False
    ):
        if allow_control is not True:
            raise PermissionError("Owned process control is disabled by default")
        self.argv = list(map(str, argv))
        self.events = []
        self.debug_pid = None
        self.debug_birth = None
        self.debug_event = None
        self.writer = None
        self.debug_handles = []
        self.pipe_fd = None
        self.closed = False
        self.controlled_termination = False
        self.finalization = None
        self.job = checked(create_job(None, None))
        limits = Limits()
        limits.basic.flags = 0x2000  # Kill only this owned tree if the controller exits.
        checked(set_job(self.job, 9, C.byref(limits), C.sizeof(limits)))
        stdin = os.open(os.devnull, os.O_RDONLY)
        err = os.open(str(stderr_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY)
        if pipe:
            self.pipe_fd, out = os.pipe()
            self.stdout_path = Path(stdout_path)
            assert not self.stdout_path.exists()
        else:
            out = os.open(str(stdout_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY)
        for fd in [stdin, out, err]:
            os.set_inheritable(fd, True)
        si = Startup()
        si.cb = C.sizeof(si)
        si.flags = 0x100
        si.stdin, si.stdout, si.stderr = [msvcrt.get_osfhandle(fd) for fd in [stdin, out, err]]
        info = ProcessInfo()
        command = C.create_unicode_buffer(subprocess.list2cmdline(self.argv))
        environment = C.create_unicode_buffer(
            "\0".join(k + "=" + str(env[k]) for k in sorted(env, key=str.upper)) + "\0\0"
        )
        try:
            checked(
                create_process(
                    self.argv[0],
                    command,
                    None,
                    None,
                    True,
                    0x08000000 | 0x00000400 | 0x00000004,
                    environment,
                    str(cwd),
                    C.byref(si),
                    C.byref(info),
                )
            )
            self.process = info.process
            self.pid = info.pid
            times = [C.c_ulonglong() for _ in range(4)]
            checked(get_times(self.process, *[C.byref(t) for t in times]))
            self.creation_filetime = times[0].value
            checked(assign_job(self.job, self.process))
            self.events.append(
                {
                    "at": now(),
                    "action": "created_suspended_and_assigned_owned_job",
                    "pid": self.pid,
                    "tid": info.tid,
                    "creation_filetime": self.creation_filetime,
                }
            )
            if resume_thread(info.thread) == 0xFFFFFFFF:
                raise C.WinError(C.get_last_error())
            close(info.thread)
        except BaseException:
            if info.process:
                terminate_process(info.process, 0xE0424331)
                wait_handle(info.process, 10000)
                close(info.process)
            if info.thread:
                close(info.thread)
            close(self.job)
            raise
        finally:
            for fd in [stdin, out, err]:
                os.close(fd)

    def running(self):
        return wait_handle(self.process, 0) == 258

    def exit_code(self):
        code = W.DWORD()
        checked(get_exit(self.process, C.byref(code)))
        return code.value

    def pipe_available(self):
        count = W.DWORD()
        if not peek_pipe(msvcrt.get_osfhandle(self.pipe_fd), None, 0, None, C.byref(count), None):
            if C.get_last_error() == 109:
                return 0
            raise C.WinError(C.get_last_error())
        return count.value

    def identify(self, pid):
        handle = checked(open_process(0x00100000 | 0x1000, False, pid))
        try:
            member = W.BOOL()
            checked(in_job(handle, self.job, C.byref(member)))
            if not member.value or wait_handle(handle, 0) != 258:
                raise RuntimeError("Process is not a live member of this owned job")
            times = [C.c_ulonglong() for _ in range(4)]
            checked(get_times(handle, *[C.byref(t) for t in times]))
            size = W.DWORD(32768)
            name = C.create_unicode_buffer(size.value)
            checked(image_name(handle, 0, name, C.byref(size)))
            return {
                "pid": pid,
                "creation_filetime": times[0].value,
                "image": name.value,
                "owned_job_member": True,
            }
        finally:
            close(handle)

    def freeze(self, pid, expected_image, *, expected_birth):
        actual = self.identify(pid)
        if expected_birth is None or actual["creation_filetime"] != expected_birth:
            raise RuntimeError("PID birth identity changed; refusing debug attachment")
        self.writer = checked(open_process(0x00100000 | 0x1000, False, pid))
        member = W.BOOL()
        checked(in_job(self.writer, self.job, C.byref(member)))
        if not member.value or wait_handle(self.writer, 0) != 258:
            raise RuntimeError("The requested writer is not a live member of this owned job")
        times = [C.c_ulonglong() for _ in range(4)]
        checked(get_times(self.writer, *[C.byref(t) for t in times]))
        if times[0].value != expected_birth:
            raise RuntimeError("Held writer handle has a different birth; refusing attachment")
        size = W.DWORD(32768)
        name = C.create_unicode_buffer(size.value)
        checked(image_name(self.writer, 0, name, C.byref(size)))
        if Path(name.value).resolve() != Path(expected_image).resolve():
            raise RuntimeError("Writer executable identity differs from the declared binary")
        checked(debug_attach(pid))
        self.debug_pid = pid
        self.debug_birth = expected_birth
        checked(debug_kill_on_exit(False))
        event = DebugEvent()
        checked(debug_wait(C.byref(event), 5000))
        if event.pid != pid:
            raise RuntimeError("Unexpected debug event process")
        self.debug_event = event
        if (
            expected_birth != self.debug_birth
            or self.identify(pid)["creation_filetime"] != expected_birth
        ):
            checked(debug_continue(event.pid, event.tid, 0x00010002))
            self.debug_event = None
            checked(debug_stop(pid))
            self.debug_pid = None
            raise RuntimeError("Writer identity changed during attachment")
        if event.code == 3:
            # Process/thread debug handles close when their exit event continues.
            self.debug_handles.extend(h for h in [event.info.created.file] if h)
        self.events.append(
            {
                "at": now(),
                "action": "debug_event_held_all_threads_suspended",
                "pid": pid,
                "tid": event.tid,
                "event_code": event.code,
                "image": name.value,
                "owned_job_member": True,
                "creation_filetime": expected_birth,
                "birth_rechecked": True,
            }
        )

    def stop(self, exit_code=0xE0424331):
        if self.closed:
            raise RuntimeError("Owned job is already closed")
        before = self.tree_snapshot()
        self.controlled_termination = True
        checked(terminate_job(self.job, exit_code))
        self.events.append(
            {
                "at": now(),
                "action": "terminate_owned_job",
                "requested_exit": exit_code,
                "external_controlled": True,
                "members_before_termination": before,
            }
        )
        if self.debug_event is not None:
            e = self.debug_event
            checked(debug_continue(e.pid, e.tid, 0x00010002))
            self.debug_event = None
        if self.debug_pid is not None:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                e = DebugEvent()
                if debug_wait(C.byref(e), 100):
                    row = {"at": now(), "event_code": e.code, "pid": e.pid, "tid": e.tid}
                    if e.code == 1:
                        row["exception_code"] = e.info.exception.record.code
                        row["first_chance"] = e.info.exception.first_chance
                    self.events.append(row)
                    if e.code == 6 and e.info.file:
                        close(e.info.file)
                    checked(debug_continue(e.pid, e.tid, 0x00010002))
                    if e.code == 5:
                        break
                elif not self.running():
                    break
            debug_stop(self.debug_pid)
            self.debug_pid = None
        if wait_handle(self.process, 10000) != 0:
            raise RuntimeError("Owned parent exit has not been observed")
        if self.writer and wait_handle(self.writer, 10000) != 0:
            raise RuntimeError("Owned writer exit has not been observed")
        if not self.wait_tree_empty(10):
            raise RuntimeError("Owned job members remain after explicit termination")
        result = {
            "parent_pid": self.pid,
            "parent_exit": self.exit_code(),
            "external_requested_exit": exit_code,
        }
        if self.writer:
            value = W.DWORD()
            checked(get_exit(self.writer, C.byref(value)))
            result["writer_exit"] = value.value
        self.events.append({"at": now(), "action": "all_observed_handles_signalled", **result})
        return result

    def resume(self, *, expected_birth):
        """Release only our held event after rechecking the live owned writer."""
        if self.debug_pid is None or self.debug_event is None:
            raise RuntimeError("No owned suspended writer to resume")
        pid = self.debug_pid
        if (
            expected_birth != self.debug_birth
            or self.identify(pid)["creation_filetime"] != expected_birth
        ):
            raise RuntimeError("Writer birth changed; refusing resume")
        event = self.debug_event
        checked(debug_continue(event.pid, event.tid, 0x00010002))
        self.debug_event = None
        checked(debug_stop(pid))
        self.debug_pid = None
        self.debug_birth = None
        for handle in self.debug_handles:
            close(handle)
        self.debug_handles.clear()
        if self.writer:
            close(self.writer)
            self.writer = None
        self.events.append(
            {
                "at": now(),
                "action": "owned_writer_resumed_and_debugger_detached",
                "pid": pid,
                "creation_filetime": expected_birth,
                "birth_rechecked": True,
            }
        )

    def close(self):
        if self.closed:
            return self.finalization
        before = self.tree_snapshot()
        if before["members"] or self.debug_pid is not None:
            self.stop()
        after = self.tree_snapshot()
        if after["members"] or self.running():
            raise RuntimeError("Refuse Job close while owned members remain")
        self.finalization = {
            "natural_tree_complete": not self.controlled_termination,
            "controlled_termination": self.controlled_termination,
            "before_close": before,
            "after_cleanup": after,
            "root_pid": self.pid,
            "root_birth": self.creation_filetime,
            "root_exit": self.exit_code(),
        }
        if self.pipe_fd is not None:
            os.close(self.pipe_fd)
            self.pipe_fd = None
        for handle in self.debug_handles:
            close(handle)
        if self.writer:
            close(self.writer)
        checked(close(self.process))
        checked(close(self.job))
        self.closed = True
        self.events.append({"at": now(), "action": "empty_owned_job_closed", **self.finalization})
        return self.finalization

    def member_ids(self):
        """Kernel membership list, not ancestry guessed from a reusable PID."""
        for capacity in (16, 64, 256, 1024, 4096):

            class JobMembers(C.Structure):
                _fields_ = [("assigned", W.DWORD), ("count", W.DWORD), ("ids", SIZE * capacity)]

            value = JobMembers()
            if query_job(self.job, 3, C.byref(value), C.sizeof(value), None):
                if value.assigned == value.count and value.count <= capacity:
                    return [int(pid) for pid in value.ids[: value.count]]
            elif C.get_last_error() != 234:  # ERROR_MORE_DATA: a growing owned tree
                raise C.WinError(C.get_last_error())
        raise RuntimeError("Owned member list exceeded the bounded attribution capacity")

    def tree_snapshot(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            ids = self.member_ids()
            members = []
            raced = False
            for pid in ids:
                try:
                    members.append(self.identify(pid))
                except (OSError, RuntimeError):
                    raced = True
                    break
            # A held process can exit during attribution. Take a new kernel
            # snapshot rather than accepting a foreign reused PID or stale row.
            if not raced and self.member_ids() == ids:
                return {
                    "at": now(),
                    "source": "JobObjectBasicProcessIdList",
                    "members": members,
                    "all_members_attributed": True,
                }
            time.sleep(0.001)
        raise RuntimeError("Could not obtain a stable, attributed owned Job member set")

    def wait_tree_empty(self, timeout):
        deadline = time.monotonic() + timeout
        while self.member_ids():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True
