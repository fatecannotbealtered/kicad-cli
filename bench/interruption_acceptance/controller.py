"""Windows x64 owned-Job, pre-WriteFile hardware execution boundary.

No attach API, product import, disk patch, WriteProcessMemory, process-wide
enumeration, privilege change, or time-based success trigger exists here.
Raw candidate/caller bytes stay in a new restricted private directory.
"""

import ctypes as C
import datetime as DT
import hashlib
import json
import msvcrt
import os
import struct
import subprocess
import time
from ctypes import wintypes as W
from pathlib import Path

H, D, Q, S = C.c_void_p, C.c_uint32, C.c_uint64, C.c_size_t
CONTINUE, NOT_HANDLED = 0x10002, 0x80010001
STOP_CODE = 0xE0424931
CONTEXT_FLAGS = 0x100013  # AMD64 | CONTROL | INTEGER | DEBUG_REGISTERS


def now():
    return DT.datetime.now(DT.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


class Startup(C.Structure):
    _fields_ = [
        ("cb", D),
        ("reserved", H),
        ("desktop", H),
        ("title", H),
        ("x", D),
        ("y", D),
        ("xs", D),
        ("ys", D),
        ("xc", D),
        ("yc", D),
        ("fill", D),
        ("flags", D),
        ("show", C.c_uint16),
        ("reserved_size", C.c_uint16),
        ("reserved_bytes", H),
        ("stdin", H),
        ("stdout", H),
        ("stderr", H),
    ]


class ProcessInfo(C.Structure):
    _fields_ = [("process", H), ("thread", H), ("pid", D), ("tid", D)]


class StartupEx(C.Structure):
    _fields_ = [("startup", Startup), ("attributes", H)]


class BasicLimits(C.Structure):
    _fields_ = [
        ("process_time", Q),
        ("job_time", Q),
        ("flags", D),
        ("min_ws", S),
        ("max_ws", S),
        ("active_limit", D),
        ("affinity", S),
        ("priority", D),
        ("scheduling", D),
    ]


class ExtendedLimits(C.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", Q * 6),
        ("process_memory", S),
        ("job_memory", S),
        ("peak_process", S),
        ("peak_job", S),
    ]


class ExceptionRecord(C.Structure):
    _fields_ = [
        ("code", D),
        ("flags", D),
        ("record", H),
        ("address", H),
        ("count", D),
        ("information", Q * 15),
    ]


class ExceptionInfo(C.Structure):
    _fields_ = [("record", ExceptionRecord), ("first_chance", D)]


class CreateInfo(C.Structure):
    _fields_ = [
        ("file", H),
        ("process", H),
        ("thread", H),
        ("base", H),
        ("debug_offset", D),
        ("debug_size", D),
        ("thread_base", H),
        ("start", H),
        ("image_name", H),
        ("unicode", C.c_uint16),
    ]


class ThreadInfo(C.Structure):
    _fields_ = [("thread", H), ("base", H), ("start", H)]


class LoadInfo(C.Structure):
    _fields_ = [
        ("file", H),
        ("base", H),
        ("debug_offset", D),
        ("debug_size", D),
        ("name", H),
        ("unicode", C.c_uint16),
    ]


class EventData(C.Union):
    _fields_ = [
        ("exception", ExceptionInfo),
        ("create", CreateInfo),
        ("thread", ThreadInfo),
        ("load", LoadInfo),
        ("exit_code", D),
        ("storage", C.c_byte * 160),
    ]


class Event(C.Structure):
    _fields_ = [("code", D), ("pid", D), ("tid", D), ("data", EventData)]


class UnicodeString(C.Structure):
    _fields_ = [("length", C.c_uint16), ("maximum", C.c_uint16), ("buffer", H)]


class SecurityAttributes(C.Structure):
    _fields_ = [("size", D), ("descriptor", H), ("inherit", W.BOOL)]


class Context:
    """Exactly 1232 AMD64 bytes, with the required 16-byte alignment."""

    def __init__(self):
        self.storage = C.create_string_buffer(1232 + 15)
        self.address = (C.addressof(self.storage) + 15) & ~15
        self.set32(48, CONTEXT_FLAGS)

    def get(self, offset):
        return Q.from_address(self.address + offset).value

    def set(self, offset, value):
        Q.from_address(self.address + offset).value = value

    def get32(self, offset):
        return D.from_address(self.address + offset).value

    def set32(self, offset, value):
        D.from_address(self.address + offset).value = value


class APIs:
    """Every native call has an explicit ABI. All targets are own known handles."""

    def __init__(self):
        if os.name != "nt" or C.sizeof(H) != 8:
            raise RuntimeError("Windows x64 controller required")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.n = C.WinDLL("ntdll", use_last_error=True)
        self.a = C.WinDLL("advapi32", use_last_error=True)
        self.b = C.WinDLL(
            str(Path(os.environ["SystemRoot"]) / "System32/KernelBase.dll"), use_last_error=True
        )

        def bind(dll, name, args, result):
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, result
            setattr(self, name, fn)

        for name, args, result in [
            ("CreateJobObjectW", [H, W.LPCWSTR], H),
            ("SetInformationJobObject", [H, C.c_int, H, D], W.BOOL),
            ("QueryInformationJobObject", [H, C.c_int, H, D, H], W.BOOL),
            ("AssignProcessToJobObject", [H, H], W.BOOL),
            ("InitializeProcThreadAttributeList", [H, D, D, C.POINTER(S)], W.BOOL),
            ("UpdateProcThreadAttribute", [H, D, S, H, S, H, H], W.BOOL),
            ("DeleteProcThreadAttributeList", [H], None),
            ("IsProcessInJob", [H, H, C.POINTER(W.BOOL)], W.BOOL),
            ("TerminateJobObject", [H, W.UINT], W.BOOL),
            (
                "CreateProcessW",
                [
                    W.LPCWSTR,
                    W.LPWSTR,
                    H,
                    H,
                    W.BOOL,
                    D,
                    H,
                    W.LPCWSTR,
                    C.POINTER(Startup),
                    C.POINTER(ProcessInfo),
                ],
                W.BOOL,
            ),
            ("ResumeThread", [H], D),
            ("GetProcessTimes", [H, H, H, H, H], W.BOOL),
            ("QueryFullProcessImageNameW", [H, D, W.LPWSTR, C.POINTER(D)], W.BOOL),
            ("IsWow64Process", [H, C.POINTER(W.BOOL)], W.BOOL),
            ("WaitForDebugEvent", [C.POINTER(Event), D], W.BOOL),
            ("ContinueDebugEvent", [D, D, D], W.BOOL),
            ("GetThreadContext", [H, H], W.BOOL),
            ("SetThreadContext", [H, H], W.BOOL),
            ("ReadProcessMemory", [H, H, H, S, C.POINTER(S)], W.BOOL),
            ("GetFinalPathNameByHandleW", [H, W.LPWSTR, D, D], D),
            ("PeekNamedPipe", [H, H, D, H, C.POINTER(D), H], W.BOOL),
            ("WaitForSingleObject", [H, D], D),
            ("GetExitCodeProcess", [H, C.POINTER(D)], W.BOOL),
            ("GetCurrentProcess", [], H),
            ("DuplicateHandle", [H, H, H, C.POINTER(H), D, W.BOOL, D], W.BOOL),
            ("CloseHandle", [H], W.BOOL),
            ("LocalFree", [H], H),
            ("CreateDirectoryW", [W.LPCWSTR, C.POINTER(SecurityAttributes)], W.BOOL),
        ]:
            bind(self.k, name, args, result)
        bind(self.n, "NtQueryInformationProcess", [H, D, H, D, C.POINTER(D)], C.c_int32)
        bind(self.b, "CompareObjectHandles", [H, H], W.BOOL)
        bind(self.a, "OpenProcessToken", [H, D, C.POINTER(H)], W.BOOL)
        bind(self.a, "GetTokenInformation", [H, C.c_int, H, D, C.POINTER(D)], W.BOOL)
        bind(self.a, "ConvertSidToStringSidW", [H, C.POINTER(H)], W.BOOL)
        bind(
            self.a,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [W.LPCWSTR, D, C.POINTER(H), C.POINTER(D)],
            W.BOOL,
        )
        assert C.sizeof(Event) == 176 and C.sizeof(ExceptionRecord) == 152

    def private_directory(self, path):
        token, sid_string, descriptor = H(), H(), H()
        checked(self.OpenProcessToken(self.GetCurrentProcess(), 8, C.byref(token)))
        try:
            size = D()
            self.GetTokenInformation(token, 1, None, 0, C.byref(size))
            buffer = C.create_string_buffer(size.value)
            checked(self.GetTokenInformation(token, 1, buffer, size, C.byref(size)))
            sid = H.from_buffer(buffer).value
            checked(self.ConvertSidToStringSidW(sid, C.byref(sid_string)))
            sid_text = C.wstring_at(sid_string)
            # Protected DACL; only this user and SYSTEM, inherited by new files.
            sddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;" + sid_text + ")"
            checked(
                self.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl, 1, C.byref(descriptor), None
                )
            )
            security = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
            checked(self.CreateDirectoryW(str(path), C.byref(security)))
        finally:
            if descriptor:
                self.LocalFree(descriptor)
            if sid_string:
                self.LocalFree(sid_string)
            self.CloseHandle(token)

    def context(self, thread):
        context = Context()
        checked(self.GetThreadContext(thread, context.address))
        return context

    def identity(self, handle):
        birth = [Q() for _ in range(4)]
        checked(self.GetProcessTimes(handle, *[C.byref(item) for item in birth]))
        name, size = C.create_unicode_buffer(32768), D(32768)
        checked(self.QueryFullProcessImageNameW(handle, 0, name, C.byref(size)))
        wow = W.BOOL()
        checked(self.IsWow64Process(handle, C.byref(wow)))
        if wow.value:
            raise RuntimeError("WOW64 process refused")
        basic = C.create_string_buffer(48)
        returned = D()
        status = self.NtQueryInformationProcess(handle, 0, basic, 48, C.byref(returned))
        if status < 0:
            raise RuntimeError("Own-process basic information failed: " + hex(status & 0xFFFFFFFF))
        parent_pid = struct.unpack_from("<Q", basic, 40)[0]
        required = D()
        self.NtQueryInformationProcess(handle, 60, None, 0, C.byref(required))
        if not 16 <= required.value <= 131088:
            raise RuntimeError("Own-process command line size unavailable")
        command = C.create_string_buffer(required.value)
        status = self.NtQueryInformationProcess(
            handle, 60, command, len(command), C.byref(required)
        )
        if status < 0:
            raise RuntimeError("Own-process command line query failed: " + hex(status & 0xFFFFFFFF))
        text = UnicodeString.from_buffer(command)
        start, end = C.addressof(command), C.addressof(command) + len(command)
        if text.length % 2 or not start <= int(text.buffer or 0) <= end - text.length:
            raise RuntimeError("Own-process command line pointer refused")
        return {
            "creation_filetime": birth[0].value,
            "image": name.value,
            "image_disk_sha256": sha(name.value),
            "command_line": C.wstring_at(text.buffer, text.length // 2),
            "parent_pid": parent_pid,
            "x64_not_wow64": True,
        }


def pe_export(path, symbol):
    """Resolve a non-forwarded export RVA from the declared on-disk AMD64 image."""
    raw = Path(path).read_bytes()

    def u16(offset):
        return struct.unpack_from("<H", raw, offset)[0]

    def u32(offset):
        return struct.unpack_from("<I", raw, offset)[0]

    if raw[:2] != b"MZ":
        raise ValueError("Not a PE image")
    pe = u32(0x3C)
    if raw[pe : pe + 4] != b"PE\0\0" or u16(pe + 4) != 0x8664:
        raise ValueError("Not AMD64 PE")
    opt, sections, optional_size = pe + 24, u16(pe + 6), u16(pe + 20)
    if u16(opt) != 0x20B or not 1 <= sections <= 96:
        raise ValueError("Not bounded PE32+")
    export_rva, export_size = u32(opt + 112), u32(opt + 116)
    table = opt + optional_size

    def offset(rva):
        if rva < u32(opt + 60):
            return rva
        for index in range(sections):
            sec = table + index * 40
            virtual, virtual_size = u32(sec + 12), u32(sec + 8)
            size, ptr = u32(sec + 16), u32(sec + 20)
            if virtual <= rva < virtual + max(virtual_size, size):
                if rva - virtual >= size:
                    raise ValueError("RVA has no file bytes")
                return ptr + rva - virtual
        raise ValueError("RVA not mapped")

    exp = offset(export_rva)
    functions, names = u32(exp + 20), u32(exp + 24)
    if not 0 < functions <= 200000 or not 0 < names <= 200000:
        raise ValueError("Export count refused")
    funcs, name_table, ords = offset(u32(exp + 28)), offset(u32(exp + 32)), offset(u32(exp + 36))
    for index in range(names):
        name_offset = offset(u32(name_table + 4 * index))
        name_end = raw.index(b"\0", name_offset, name_offset + 512)
        if raw[name_offset:name_end] == symbol.encode("ascii"):
            ordinal = u16(ords + 2 * index)
            if ordinal >= functions:
                raise ValueError("Export ordinal out of range")
            rva = u32(funcs + 4 * ordinal)
            if export_rva <= rva < export_rva + export_size:
                raise ValueError("Forwarded export refused")
            if not 0 < rva < u32(opt + 56):
                raise ValueError("Export RVA outside image")
            return {
                "path": str(Path(path).resolve()),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "export": symbol,
                "export_rva": rva,
                "image_size": u32(opt + 56),
                "machine": "AMD64",
                "loaded_bytes_authenticated": False,
            }
    raise ValueError("Export not found")


def own_single_step(first_chance, code, dr6, rip, address):
    # Reserved DR6 bits can be one. B1/B2/B3, BD/BS/BT mean an additional cause;
    # do not swallow an application's debug exception with mixed causes.
    return bool(
        first_chance and code == 0x80000004 and dr6 & 1 and not dr6 & 0xE00E and rip == address
    )


def dword_argument(register):
    """Windows x64 integer arguments are right-justified; WriteFile takes DWORD."""
    return register & 0xFFFFFFFF


def _run_impl(
    argv,
    cwd,
    env,
    output,
    *,
    predicate,
    stage_witness,
    timeout=15,
    protected_handles=(),
    allow_owned_termination=False,
    _apis,
    _resources,
):
    """Launch a NEW process; stop only if the held pre-stdout boundary is proven.

    predicate(bytes)->bool is evaluated only on the known inherited stdout handle.
    stage_witness(boundary_metadata)->dict MUST provide qualified=True only after its exact
    operation, saved output, and committed receipt checks. It is called while
    the target's debug event remains held. Nothing here changes product success.
    """
    if allow_owned_termination is not True:
        raise PermissionError("Explicit owned interruption control required")
    a = _apis
    observer_birth = [Q() for _ in range(4)]
    checked(a.GetProcessTimes(a.GetCurrentProcess(), *[C.byref(t) for t in observer_birth]))
    observer_instance = "controller:" + str(os.getpid()) + ":" + str(observer_birth[0].value)
    output = Path(output).resolve()
    output.mkdir(exist_ok=False)
    a.private_directory(output / "private")
    trusted = pe_export(Path(os.environ["SystemRoot"]) / "System32/KernelBase.dll", "WriteFile")
    result = {
        "schema_version": "1.0",
        "method": "x64 DR0 KernelBase!WriteFile execution",
        "started_at": now(),
        "trusted_disk_export": trusted,
        "matched": False,
        "timed_out": False,
        "termination_requested": False,
        "events": [],
        "processes": [],
        "modules": [],
        "thread_installations": [],
        "candidate_boundaries": [],
        "application_exceptions": [],
        "debug_register_observations": [],
        "non_stdout_writefile_hits": 0,
        "stdout_nonmatching_hits": 0,
    }
    job, root = None, ProcessInfo()
    threads, handles, active, retained, private_identities, history = {}, {}, {}, {}, {}, {}
    bootstrap, addresses, ntdll = set(), {}, {}
    held, current_row, fds = None, None, []
    read_fd = None
    stdout_reference = H()
    journal = (output / "events.jsonl").open("x", encoding="utf-8", newline="\n")

    def public_identity(identity):
        return {
            **{k: v for k, v in identity.items() if k != "command_line"},
            "command_line_sha256": hashlib.sha256(
                identity["command_line"].encode("utf-16-le")
            ).hexdigest(),
            "raw_command_line_private": True,
        }

    def member_ids():
        class Members(C.Structure):
            _fields_ = [("assigned", D), ("count", D), ("ids", S * 256)]

        value = Members()
        checked(a.QueryInformationJobObject(job, 3, C.byref(value), C.sizeof(value), None))
        if value.assigned != value.count or value.count > 256:
            raise RuntimeError("Bounded Job member observation incomplete")
        return sorted(int(value.ids[i]) for i in range(value.count))

    def active_count():
        value = C.create_string_buffer(48)
        checked(a.QueryInformationJobObject(job, 1, value, len(value), None))
        return struct.unpack_from("<I", value, 40)[0]

    def in_job(handle):
        value = W.BOOL()
        checked(a.IsProcessInJob(handle, job, C.byref(value)))
        return bool(value.value)

    def available():
        value = D()
        if not a.PeekNamedPipe(msvcrt.get_osfhandle(read_fd), None, 0, None, C.byref(value), None):
            if C.get_last_error() == 109:
                return 0
            raise C.WinError(C.get_last_error())
        return value.value

    def snapshot():
        ids = member_ids()
        if set(ids) != set(active):
            raise RuntimeError("Live Job members do not equal CREATE-without-EXIT instances")
        members = []
        for pid in ids:
            row = active[pid]
            identity = a.identity(handles[pid])
            if identity != private_identities[row["instance_id"]] or not in_job(handles[pid]):
                raise RuntimeError("Live Job member identity changed")
            if a.WaitForSingleObject(handles[pid], 0) != 258:
                raise RuntimeError("Expected live Job member has exited")
            if pid != root.pid:
                parent = history.get(row["parent_instance_id"])
                if (
                    parent is None
                    or parent["pid"] != identity["parent_pid"]
                    or parent["identity"]["creation_filetime"] > identity["creation_filetime"]
                ):
                    raise RuntimeError("CREATE-bound historical parent instance is inconsistent")
            members.append(
                {
                    "pid": pid,
                    "instance_id": row["instance_id"],
                    "parent_instance_id": row["parent_instance_id"],
                    "identity": public_identity(identity),
                    "owned_job_member": True,
                }
            )
        if member_ids() != ids or active_count() != len(ids):
            raise RuntimeError("Job member set changed during attribution")
        protection = [
            {
                "outside_owned_job": not in_job(handle),
                "still_running": a.WaitForSingleObject(handle, 0) == 258,
            }
            for handle in protected_handles
        ]
        if any(not p["outside_owned_job"] or not p["still_running"] for p in protection):
            raise RuntimeError("Protected process is not outside live owned Job")
        return {
            "at": now(),
            "source": "own Job list + held CREATE handles",
            "all_members_attributed": True,
            "members": members,
            "protected_processes": protection,
        }

    def install(pid, tid, reason):
        handle = threads[(pid, tid)]
        context = a.context(handle)
        address = addresses[pid]
        existing = context.get(112)
        if existing & 0xFF and not (context.get(72) == address and existing & 0xFF == 1):
            raise RuntimeError("Existing hardware debug state conflicts with owned DR0")
        context.set(72, address)
        context.set(104, 0)
        context.set(112, (existing & ~0xF0003) | 1)
        checked(a.SetThreadContext(handle, context.address))
        actual = a.context(handle)
        if actual.get(72) != address or actual.get(112) & 0xF0003 != 1:
            raise RuntimeError("DR0/DR7 readback failed")
        result["thread_installations"].append(
            {
                "pid": pid,
                "tid": tid,
                "at": now(),
                "reason": reason,
                "address": hex(address),
                "dr7": hex(actual.get(112)),
                "readback_verified": True,
                "context_bytes": 1232,
                "alignment": 16,
                "flags": hex(CONTEXT_FLAGS),
            }
        )

    def is_stdout_object(pid, handle_value):
        if handle_value != stdout_handle:
            return False
        remote_copy = H()
        checked(
            a.DuplicateHandle(
                handles[pid], handle_value, a.GetCurrentProcess(), C.byref(remote_copy), 0, False, 2
            )
        )
        try:
            same = bool(a.CompareObjectHandles(stdout_reference, remote_copy))
            result.setdefault("stdout_object_comparisons", []).append(
                {"pid": pid, "same_kernel_object": same, "at": now()}
            )
            return same
        finally:
            checked(a.CloseHandle(remote_copy))

    def module(pid, handle, base):
        path = None
        try:
            if handle:
                value = C.create_unicode_buffer(32768)
                length = a.GetFinalPathNameByHandleW(handle, value, len(value), 0)
                if 0 < length < len(value):
                    path = value.value.removeprefix("\\\\?\\")
        finally:
            if handle:
                checked(a.CloseHandle(handle))
        row = {"pid": pid, "base": hex(int(base or 0)), "path": path, "at": now()}
        result["modules"].append(row)
        if path and Path(path).name.lower() == "ntdll.dll":
            # Only range metadata, not loaded-byte authenticity.
            raw = Path(path).read_bytes()
            offset = struct.unpack_from("<I", raw, 0x3C)[0] + 24
            ntdll[pid] = (int(base), struct.unpack_from("<I", raw, offset + 56)[0])
        if path and Path(path).name.lower() == "kernelbase.dll":
            if os.path.normcase(str(Path(path).resolve())) != os.path.normcase(trusted["path"]):
                raise RuntimeError("Loaded KernelBase path differs from declared System32 image")
            if pid in addresses:
                raise RuntimeError("Duplicate KernelBase load refused")
            addresses[pid] = int(base) + trusted["export_rva"]
            row.update(
                export_address=hex(addresses[pid]),
                disk_sha256=trusted["sha256"],
                loaded_bytes_authenticated=False,
            )
            for p, tid in list(threads):
                if p == pid:
                    install(pid, tid, "KernelBase LOAD_DLL before continue")

    def received(event):
        row = {
            "index": len(result["events"]),
            "at": now(),
            "code": int(event.code),
            "pid": int(event.pid),
            "tid": int(event.tid),
            "continued": False,
        }
        if event.pid in active:
            row["instance_id"] = active[event.pid]["instance_id"]
        if event.code == 1:
            row.update(
                exception_code=hex(event.data.exception.record.code),
                first_chance=bool(event.data.exception.first_chance),
            )
        result["events"].append(row)
        journal.write(json.dumps({"kind": "received", **row}) + "\n")
        journal.flush()
        return row

    def continuation(event, row, status):
        checked(a.ContinueDebugEvent(event.pid, event.tid, status))
        row.update(continued=True, continuation=hex(status))
        journal.write(json.dumps({"kind": "continued", **row}) + "\n")
        journal.flush()

    def terminate(reason):
        # No independent PID lookup or process termination API.
        result["termination_snapshot"] = snapshot()
        checked(a.TerminateJobObject(job, STOP_CODE))
        result.update(
            termination_requested=True,
            termination_reason=reason,
            termination_exit_code=STOP_CODE,
            termination_at=now(),
        )

    def handle_event(event, row, cleanup=False):
        status = CONTINUE
        pid, tid = int(event.pid), int(event.tid)
        if event.code not in (3, 4, 5) and (pid, tid) in threads and pid in addresses:
            observed = a.context(threads[(pid, tid)])
            result["debug_register_observations"].append(
                {
                    "event_index": row["index"],
                    "pid": pid,
                    "tid": tid,
                    "dr0": hex(observed.get(72)),
                    "dr6": hex(observed.get(104)),
                    "dr7": hex(observed.get(112)),
                    "context_flags": hex(observed.get32(48)),
                    "rip": hex(observed.get(248)),
                }
            )
        # On this Windows loader, NtContinue after the initial breakpoint
        # restores a saved initial CONTEXT and clears the initial thread's DRs.
        # Reconcile at later kernel debug events, while that process is stopped.
        # This is event-driven, never a timer or a polling trigger. If no such
        # event exists before output, no boundary is claimed.
        if not cleanup and pid in bootstrap and pid in addresses and event.code in (2, 6, 7, 8):
            for p, thread in list(threads):
                if p == pid:
                    context = a.context(threads[(p, thread)])
                    if context.get(72) != addresses[pid] or context.get(112) & 0xF0003 != 1:
                        row.setdefault("event_dr0_reconciliations", []).append(thread)
                        install(
                            pid, thread, "post-loader debug event repairs restored initial CONTEXT"
                        )
        if event.code == 3:
            info = event.data.create
            if not in_job(info.process):
                raise RuntimeError("CREATE event not in own new Job")
            identity = a.identity(info.process)
            if pid in active:
                raise RuntimeError("Overlapping PID CREATE")
            parent = active.get(identity["parent_pid"])
            if pid != root.pid and parent is None:
                raise RuntimeError("Child parent instance was not observed")
            if pid == root.pid and identity["command_line"] != subprocess.list2cmdline(
                list(map(str, argv))
            ):
                raise RuntimeError("Root command line differs from frozen launch")
            if pid == root.pid and identity["parent_pid"] != os.getpid():
                raise RuntimeError("Root parent does not match the held controller instance")
            entry = {
                "pid": pid,
                "instance_id": str(pid) + ":" + str(identity["creation_filetime"]),
                "identity": public_identity(identity),
                "create_event_index": row["index"],
                "parent_instance_id": parent["instance_id"] if parent else observer_instance,
                "exit_code": None,
            }
            duplicate = H()
            checked(
                a.DuplicateHandle(
                    a.GetCurrentProcess(),
                    info.process,
                    a.GetCurrentProcess(),
                    C.byref(duplicate),
                    0,
                    False,
                    2,
                )
            )
            active[pid], handles[pid] = entry, duplicate.value
            retained[entry["instance_id"]] = duplicate.value
            _resources["handles"].append(duplicate.value)
            private_identities[entry["instance_id"]] = identity
            result["processes"].append(entry)
            history[entry["instance_id"]] = entry
            row["instance_id"] = entry["instance_id"]
            threads[(pid, tid)] = info.thread
            module(pid, info.file, info.base)
        elif event.code == 2:
            if pid not in active:
                raise RuntimeError("Thread CREATE has no process instance")
            threads[(pid, tid)] = event.data.thread.thread
            if pid in addresses and not cleanup:
                install(pid, tid, "CREATE_THREAD before user execution")
        elif event.code == 4:
            threads.pop((pid, tid), None)
            row["exit_code"] = int(event.data.exit_code)
        elif event.code == 5:
            entry = active.pop(pid)
            entry.update(
                exit_code=int(event.data.exit_code), exit_event_index=row["index"], exited_at=now()
            )
            row["exit_code"] = entry["exit_code"]
            for key in list(threads):
                if key[0] == pid:
                    del threads[key]
        elif event.code == 6:
            module(pid, event.data.load.file, event.data.load.base)
        elif event.code == 7:
            base = int(event.data.load.file or 0)  # UNLOAD_DLL union begins with base
            if pid in addresses and base == addresses[pid] - trusted["export_rva"]:
                raise RuntimeError("KernelBase unload invalidates control boundary")
        elif event.code == 1:
            info = event.data.exception
            code, first = int(info.record.code), bool(info.first_chance)
            fault = int(info.record.address or 0)
            low, size = ntdll.get(pid, (0, 0))
            loader_break = (
                first and code == 0x80000003 and pid not in bootstrap and low <= fault < low + size
            )
            if loader_break:
                bootstrap.add(pid)
                row["loader_bootstrap"] = True
                if pid not in addresses:
                    raise RuntimeError("KernelBase was not observed before loader breakpoint")
                for p, thread in list(threads):
                    if p == pid:
                        install(pid, thread, "loader breakpoint before application entry")
            else:
                status = NOT_HANDLED
                if first and code == 0x80000004 and pid in addresses and (pid, tid) in threads:
                    context = a.context(threads[(pid, tid)])
                    if own_single_step(
                        first, code, context.get(104), context.get(248), addresses[pid]
                    ):
                        row.update(
                            owned_hardware_breakpoint=True,
                            rip=hex(context.get(248)),
                            dr6=hex(context.get(104)),
                            dr0=hex(context.get(72)),
                        )
                        if context.get(72) != addresses[pid] or context.get(112) & 0xF0003 != 1:
                            raise RuntimeError("Owned single-step register identity disagrees")
                        status = CONTINUE
                        candidate = None
                        if not cleanup and is_stdout_object(pid, context.get(128)):
                            count = dword_argument(context.get(184))
                            if not 0 < count <= 65536:
                                raise RuntimeError(
                                    "Stdout WriteFile buffer exceeds bounded predicate"
                                )
                            raw, read = C.create_string_buffer(count), S()
                            checked(
                                a.ReadProcessMemory(
                                    handles[pid], context.get(136), raw, count, C.byref(read)
                                )
                            )
                            if read.value != count:
                                raise RuntimeError("Short stdout buffer observation")
                            raw_bytes = bytes(raw)
                            if predicate(raw_bytes):
                                number = len(result["candidate_boundaries"])
                                private_path = (
                                    output / "private" / ("candidate-" + str(number) + ".bin")
                                )
                                private_path.write_bytes(raw_bytes)
                                candidate = {
                                    "at": now(),
                                    "event_index": row["index"],
                                    "pid": pid,
                                    "instance_id": active[pid]["instance_id"],
                                    "tid": tid,
                                    "buffer_bytes": count,
                                    "buffer_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                                    "raw_buffer_private": True,
                                    "stdout_prior_bytes": available(),
                                    "debug_event_held": True,
                                    "stdout_kernel_object_verified": True,
                                }
                                candidate["stage"] = stage_witness(dict(candidate))
                                result["candidate_boundaries"].append(candidate)
                                if (
                                    not cleanup
                                    and not result["termination_requested"]
                                    and candidate["stdout_prior_bytes"] == 0
                                    and candidate["stage"].get("qualified") is True
                                ):
                                    terminate("qualified held pre-success stdout WriteFile")
                                    result["matched"] = True
                            else:
                                result["stdout_nonmatching_hits"] += 1
                        else:
                            result["non_stdout_writefile_hits"] += 1
                        # RF suppresses only re-triggering this execution instruction;
                        # do not set TF or disable the breakpoint for a call window.
                        if not result["termination_requested"]:
                            context.set32(68, context.get32(68) | 0x10000)
                            context.set(104, 0)
                            checked(a.SetThreadContext(threads[(pid, tid)], context.address))
                            row["continued_with_rf_and_cleared_dr6"] = True
                if status == NOT_HANDLED:
                    result["application_exceptions"].append(
                        {
                            "event_index": row["index"],
                            "pid": pid,
                            "tid": tid,
                            "code": hex(code),
                            "first_chance": first,
                            "continuation": hex(NOT_HANDLED),
                        }
                    )
                    if not first or code in (0xC0000409, 0xC0000602):
                        result["safety_stop"] = {
                            "event_index": row["index"],
                            "pid": pid,
                            "tid": tid,
                            "code": hex(code),
                            "first_chance": first,
                            "reason": "unexpected second-chance or fail-fast exception",
                        }
                        result["matched"] = False
                        if not result["termination_requested"]:
                            terminate("safety stop; not a success-boundary match")
        return status

    try:
        job = checked(a.CreateJobObjectW(None, None))
        _resources["job"] = job
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000
        checked(a.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)))
        stdin_fd = os.open(os.devnull, os.O_RDONLY | os.O_BINARY)
        read_fd, out_fd = os.pipe()
        err_fd = os.open(
            str(output / "private" / "stderr.bin"),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY,
        )
        fds = [stdin_fd, out_fd, err_fd]
        for fd in fds:
            os.set_inheritable(fd, True)
        os.set_inheritable(read_fd, False)
        stdout_handle = msvcrt.get_osfhandle(out_fd)
        checked(
            a.DuplicateHandle(
                a.GetCurrentProcess(),
                stdout_handle,
                a.GetCurrentProcess(),
                C.byref(stdout_reference),
                0,
                False,
                2,
            )
        )
        _resources["handles"].append(stdout_reference.value)
        start = StartupEx()
        start.startup.cb, start.startup.flags = C.sizeof(start), 0x100
        inherited = (H * 3)(*[msvcrt.get_osfhandle(fd) for fd in fds])
        start.startup.stdin, start.startup.stdout, start.startup.stderr = inherited
        command = C.create_unicode_buffer(subprocess.list2cmdline(list(map(str, argv))))
        environment = C.create_unicode_buffer(
            "\0".join(k + "=" + str(env[k]) for k in sorted(env, key=str.upper)) + "\0\0"
        )
        required = S()
        a.InitializeProcThreadAttributeList(None, 2, 0, C.byref(required))
        if C.get_last_error() != 122 or not required.value:
            raise RuntimeError("STARTUPINFOEX attribute size unavailable")
        attribute_storage = C.create_string_buffer(required.value)
        start.attributes = C.addressof(attribute_storage)
        checked(a.InitializeProcThreadAttributeList(start.attributes, 2, 0, C.byref(required)))
        try:
            jobs = (H * 1)(job)
            checked(
                a.UpdateProcThreadAttribute(
                    start.attributes, 0, 0x2000D, jobs, C.sizeof(jobs), None, None
                )
            )
            checked(
                a.UpdateProcThreadAttribute(
                    start.attributes, 0, 0x20002, inherited, C.sizeof(inherited), None, None
                )
            )
            # Atomic job assignment means a failed assignment cannot leave a
            # newly created suspended process outside the only killable Job.
            checked(
                a.CreateProcessW(
                    str(argv[0]),
                    command,
                    None,
                    None,
                    True,
                    0x08000000 | 0x80000 | 0x400 | 4 | 1,
                    environment,
                    str(cwd),
                    C.cast(C.byref(start), C.POINTER(Startup)),
                    C.byref(root),
                )
            )
            _resources["handles"].extend([root.process, root.thread])
        finally:
            a.DeleteProcThreadAttributeList(start.attributes)
        if not in_job(root.process):
            raise RuntimeError("Atomic launch did not produce an owned Job root")
        put(output / "private" / "launch.json", {"argv": list(map(str, argv)), "cwd": str(cwd)})
        result.update(
            root_pid=int(root.pid),
            root_creation_filetime=a.identity(root.process)["creation_filetime"],
            assigned_before_resume=True,
            stdout_handle=hex(stdout_handle),
            root_assignment="atomic PROC_THREAD_ATTRIBUTE_JOB_LIST",
            inherited_handles="explicit stdout/stderr/stdin handle list only",
            launch_argv_sha256=hashlib.sha256(command.value.encode("utf-16-le")).hexdigest(),
            launch_raw_private=True,
            launch_cwd=str(cwd),
        )
        if member_ids() != [root.pid]:
            raise RuntimeError("Initial suspended Job not exactly the owned root")
        if a.ResumeThread(root.thread) == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        for fd in fds:
            os.close(fd)
        fds = []
        deadline = time.monotonic() + timeout
        cleanup_deadline = None
        while True:
            if time.monotonic() >= deadline and not result["termination_requested"]:
                result["timed_out"] = True
                terminate("bounded timeout cleanup; not boundary match")
                cleanup_deadline = time.monotonic() + 5
            if result["termination_requested"] and cleanup_deadline is None:
                cleanup_deadline = time.monotonic() + 5
            if cleanup_deadline is not None and time.monotonic() > cleanup_deadline:
                raise TimeoutError("Owned Job exit debug-event collection incomplete")
            event = Event()
            if not a.WaitForDebugEvent(C.byref(event), 50):
                if C.get_last_error() not in (0, 121):
                    raise C.WinError(C.get_last_error())
                continue
            _resources["held"] = event
            held = event
            current_row = received(event)
            status = handle_event(event, current_row, cleanup=result["termination_requested"])
            continuation(event, current_row, status)
            held = None
            _resources["held"] = None
            if event.code == 5 and not active:
                break
        result["complete_exit_debug_events"] = bool(
            result["processes"] and all(row["exit_code"] is not None for row in result["processes"])
        )
    except BaseException as exc:
        result.update(
            matched=False,
            controller_failure=type(exc).__name__,
            controller_failure_details_private=True,
        )
        if job and root.process:
            # Method errors can only clean this new owned Job. Never call this a hit.
            checked(a.TerminateJobObject(job, STOP_CODE))
            result["failure_cleanup_requested"] = True
            if held is not None:
                continuation(held, current_row, NOT_HANDLED if held.code == 1 else CONTINUE)
                held = None
                _resources["held"] = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                event = Event()
                if a.WaitForDebugEvent(C.byref(event), 50):
                    _resources["held"] = event
                    row = received(event)
                    try:
                        status = handle_event(event, row, cleanup=True)
                    except BaseException as cleanup_error:
                        row["cleanup_error"] = type(cleanup_error).__name__
                        status = NOT_HANDLED if event.code == 1 else CONTINUE
                    continuation(event, row, status)
                    _resources["held"] = None
                if not member_ids() and not active:
                    break
            result["complete_exit_debug_events"] = bool(
                result["processes"]
                and all(row["exit_code"] is not None for row in result["processes"])
            )
        try:
            put(
                output / "private" / "controller-failure.json",
                {"type": type(exc).__name__, "message": str(exc)},
            )
        except OSError:
            # Reporting cannot prevent the independent outer resource guard.
            result["private_failure_report_unavailable"] = True
    finally:
        for fd in fds:
            os.close(fd)
        if root.process:
            a.WaitForSingleObject(root.process, 1000)
            code = D()
            checked(a.GetExitCodeProcess(root.process, C.byref(code)))
            result["root_exit_code"] = code.value
        result["process_handle_finalization"] = []
        for instance, handle in retained.items():
            signaled = a.WaitForSingleObject(handle, 1000) == 0
            code = D()
            checked(a.GetExitCodeProcess(handle, C.byref(code)))
            result["process_handle_finalization"].append(
                {
                    "instance_id": instance,
                    "duplicate_handle_signaled": signaled,
                    "exit_code": code.value,
                }
            )
        if job:
            accounting_deadline = time.monotonic() + 1
            while active_count() and time.monotonic() < accounting_deadline:
                time.sleep(0.005)
            result["job_final_member_ids"] = member_ids()
            result["job_final_active_count"] = active_count()
        if read_fd is not None:
            pieces = []
            while available():
                pieces.append(os.read(read_fd, min(available(), 65536)))
            actual = b"".join(pieces)
            (output / "private" / "caller-stdout.bin").write_bytes(actual)
            result.update(
                caller_stdout_bytes=len(actual),
                caller_stdout_sha256=hashlib.sha256(actual).hexdigest(),
                caller_stdout_raw_private=True,
            )
            os.close(read_fd)
        result["protected_processes_after"] = [
            {"still_running": a.WaitForSingleObject(h, 0) == 258} for h in protected_handles
        ]
        result["finished_at"] = now()
        exit_by_instance = {row["instance_id"]: row for row in result["processes"]}
        live_at_stop = {
            row["instance_id"] for row in result.get("termination_snapshot", {}).get("members", [])
        }
        exit_proof = bool(result["process_handle_finalization"]) and all(
            row["duplicate_handle_signaled"]
            and row["exit_code"] == exit_by_instance[row["instance_id"]]["exit_code"]
            and (row["instance_id"] not in live_at_stop or row["exit_code"] == STOP_CODE)
            for row in result["process_handle_finalization"]
        )
        result["exit_proof_consistent"] = exit_proof
        result["qualified_interruption"] = bool(
            result["matched"]
            and not result.get("controller_failure")
            and not result.get("safety_stop")
            and result.get("caller_stdout_bytes") == 0
            and result.get("complete_exit_debug_events")
            and result.get("job_final_active_count") == 0
            and result.get("job_final_member_ids") == []
            and exit_proof
            and bool(live_at_stop)
        )
        journal.close()
        result["event_journal_sha256"] = sha(output / "events.jsonl")
        put(output / "private" / "process-identities.json", private_identities)
    return result


def run(
    argv,
    cwd,
    env,
    output,
    *,
    predicate,
    stage_witness,
    timeout=15,
    protected_handles=(),
    allow_owned_termination=False,
):
    """Public new-process interface; a separate finally always closes the own Job.

    No success report is published until this resource guard has completed.
    Callback evidence must be safe for the public report; callback exceptions
    remain private. See METHOD.md for the deliberately limited observation scope.
    """
    if allow_owned_termination is not True:
        raise PermissionError("Explicit owned interruption control required")
    a = APIs()
    resources = {"job": None, "handles": [], "held": None}
    result = None
    guard = {
        "safety_cleanup_used": False,
        "owned_job_handle_closed": False,
        "retained_handles_closed": True,
    }
    try:
        result = _run_impl(
            argv,
            cwd,
            env,
            output,
            predicate=predicate,
            stage_witness=stage_witness,
            timeout=timeout,
            protected_handles=protected_handles,
            allow_owned_termination=True,
            _apis=a,
            _resources=resources,
        )
    finally:
        job = resources["job"]
        try:
            if job and (result is None or result.get("job_final_active_count", 1) != 0):
                guard["safety_cleanup_used"] = True
                # Even failed evidence writes cannot bypass this own-Job stop.
                a.TerminateJobObject(job, STOP_CODE)
                held = resources["held"]
                if held is not None:
                    a.ContinueDebugEvent(
                        held.pid, held.tid, NOT_HANDLED if held.code == 1 else CONTINUE
                    )
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    event = Event()
                    if a.WaitForDebugEvent(C.byref(event), 25):
                        if event.code == 3 and event.data.create.file:
                            a.CloseHandle(event.data.create.file)
                        elif event.code == 6 and event.data.load.file:
                            a.CloseHandle(event.data.load.file)
                        a.ContinueDebugEvent(
                            event.pid, event.tid, NOT_HANDLED if event.code == 1 else CONTINUE
                        )
                    account = C.create_string_buffer(48)
                    if (
                        a.QueryInformationJobObject(job, 1, account, 48, None)
                        and struct.unpack_from("<I", account, 40)[0] == 0
                    ):
                        break
        finally:
            # Close the Job independently even if observation, reporting or a
            # different handle's finalization fails. KILL_ON_JOB_CLOSE bounds
            # every member; there is never a foreign PID fallback.
            try:
                for handle in reversed(resources["handles"]):
                    if not a.CloseHandle(handle):
                        guard["retained_handles_closed"] = False
            finally:
                if job:
                    guard["owned_job_handle_closed"] = bool(a.CloseHandle(job))
        if result is not None:
            result["resource_guard"] = guard
            if not guard["owned_job_handle_closed"] or not guard["retained_handles_closed"]:
                result["qualified_interruption"] = False
    put(Path(output) / "result.json", result)
    return result
