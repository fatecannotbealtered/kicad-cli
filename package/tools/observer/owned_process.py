"""Ephemeral ownership of a test subprocess tree; no privilege/config changes."""

import ctypes
import os
import signal
import subprocess
import time
from ctypes import wintypes as W


class WindowsJob:
    def __init__(self):
        self.control_events = []
        self.finalization = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        k.CreateJobObjectW.restype = W.HANDLE
        k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
        k.CloseHandle.argtypes = [W.HANDLE]
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
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign_and_resume(self, process):
        k = self.kernel
        if not k.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        initial = self.snapshot()
        if len(initial["members"]) != 1 or initial["members"][0]["pid"] != process.pid:
            raise RuntimeError("Initial owned Job does not contain exactly the suspended root")
        self.root_identity = dict(initial["members"][0], assigned_before_resume=True)

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
        snap = k.CreateToolhelp32Snapshot(4, 0)
        if snap == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        ids = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = k.Thread32First(snap, ctypes.byref(entry))
            while found:
                if entry.pid == process.pid:
                    ids.append(entry.tid)
                found = k.Thread32Next(snap, ctypes.byref(entry))
        finally:
            k.CloseHandle(snap)
        if len(ids) != 1:
            raise RuntimeError("Owned suspended process must have exactly one initial thread")
        thread = k.OpenThread(2, False, ids[0])
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if k.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k.CloseHandle(thread)

    def terminate(self):
        before = self.snapshot()
        if not self.kernel.TerminateJobObject(self.handle, 0xE0000001):
            raise ctypes.WinError(ctypes.get_last_error())
        self.control_events.append({"action": "terminate_owned_job", "members": before})

    def snapshot(self):
        # This file also ships beside standalone readers. Do not depend on the
        # developer checkout or a sibling package being importable.
        k = self.kernel
        k.QueryInformationJobObject.argtypes = [
            W.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            W.DWORD,
            ctypes.c_void_p,
        ]
        k.OpenProcess.argtypes, k.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
        k.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)]
        k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.c_void_p] * 4
        k.QueryFullProcessImageNameW.argtypes = [
            W.HANDLE,
            W.DWORD,
            W.LPWSTR,
            ctypes.POINTER(W.DWORD),
        ]
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]

        def checked(ok):
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            return ok

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
            raise RuntimeError("Owned Job exceeds attribution capacity")

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
                            raise RuntimeError("Process exited or membership changed")
                        times = [ctypes.c_ulonglong() for _ in range(4)]
                        checked(k.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]))
                        size = W.DWORD(32768)
                        name = ctypes.create_unicode_buffer(size.value)
                        checked(k.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)))
                        members.append(
                            {
                                "pid": pid,
                                "creation_filetime": times[0].value,
                                "image": name.value,
                                "owned_job_member": True,
                            }
                        )
                    finally:
                        k.CloseHandle(handle)
            except (OSError, RuntimeError):
                time.sleep(0.001)
                continue
            if ids() == current:
                from datetime import datetime, timezone

                return {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "source": "JobObjectBasicProcessIdList",
                    "members": members,
                    "all_members_attributed": True,
                }
        raise RuntimeError("Could not read a stable attributed Job member set")

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

    def close(self):
        if self.handle:
            before = self.snapshot()
            if before["members"]:
                self.terminate()
            deadline = time.monotonic() + 10
            while self.active() and time.monotonic() < deadline:
                time.sleep(0.01)
            after = self.snapshot()
            if after["members"]:
                raise RuntimeError("Refuse Job close while owned members remain")
            self.finalization = {
                "natural_tree_complete": not self.control_events,
                "controlled_termination": bool(self.control_events),
                "before_close": before,
                "after_cleanup": after,
                "control_events": self.control_events,
            }
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None
        return self.finalization


def spawn(command, *, debug=False, **kwargs):
    job = WindowsJob() if os.name == "nt" else None
    process = None
    try:
        if job:
            # Assignment precedes execution; no child can escape in a launch race.
            process = subprocess.Popen(
                command, creationflags=0x4 | 0x08000000 | (2 if debug else 0), **kwargs
            )
            job.assign_and_resume(process)
        else:
            process = subprocess.Popen(command, start_new_session=True, **kwargs)
        return process, job
    except BaseException:
        if process is not None:
            process.kill()  # still our own suspended/failed-start child
            process.wait(timeout=5)
        if job:
            job.close()
        raise


def terminate(process, job):
    if job:
        job.terminate()
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)
