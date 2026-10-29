"""Finite Windows AMD64 CREATE-handle metadata, with no CIM or subprocess.

PEB command lines are mutable user memory observations, not independent kernel
command truth. Kernel identity is rechecked on the same owned CREATE handle.
The documented internal structures may change: unsupported cases are incomplete.
"""

import ctypes as C
import hashlib
import os
import time
from pathlib import Path

PROVIDER = "windows-create-handle-peb-amd64/v1"
MAX_COMMAND_BYTES = 65534
MAX_SNAPSHOT_PROCESSES = 8192
MAX_REMOTE_BYTES = 2 * MAX_COMMAND_BYTES + 2 * 128 + 2 * 40
EXPECTED_LAYOUT = {
    "pbi_size": 48,
    "pbi_peb_offset": 8,
    "peb_parameters_offset": 32,
    "peb_size": 40,
    "parameters_command_offset": 112,
    "parameters_size": 128,
    "unicode_size": 16,
    "unicode_buffer_offset": 8,
    "process_entry_size": 568,
}


class MetadataIncomplete(RuntimeError):
    def __init__(self, stage, detail):
        self.stage, self.detail = stage, str(detail)
        super().__init__(stage + ": " + self.detail)


class BasicInformation(C.Structure):
    _fields_ = [
        ("ExitStatus", C.c_int32),
        ("PebBaseAddress", C.c_void_p),
        ("AffinityMask", C.c_size_t),
        ("BasePriority", C.c_int32),
        ("UniqueProcessId", C.c_size_t),
        ("InheritedFromUniqueProcessId", C.c_size_t),
    ]


class UnicodeString(C.Structure):
    _fields_ = [
        ("Length", C.c_uint16),
        ("MaximumLength", C.c_uint16),
        ("Buffer", C.c_void_p),
    ]


class PebPrefix(C.Structure):
    _fields_ = [
        ("Reserved", C.c_ubyte * 24),
        ("LoaderData", C.c_void_p),
        ("ProcessParameters", C.c_void_p),
    ]


class ParametersPrefix(C.Structure):
    _fields_ = [
        ("Reserved1", C.c_ubyte * 16),
        ("Reserved2", C.c_void_p * 10),
        ("ImagePathName", UnicodeString),
        ("CommandLine", UnicodeString),
    ]


class ProcessEntry(C.Structure):
    _fields_ = [
        ("dwSize", C.c_uint32),
        ("cntUsage", C.c_uint32),
        ("th32ProcessID", C.c_uint32),
        ("th32DefaultHeapID", C.c_size_t),
        ("th32ModuleID", C.c_uint32),
        ("cntThreads", C.c_uint32),
        ("th32ParentProcessID", C.c_uint32),
        ("pcPriClassBase", C.c_int32),
        ("dwFlags", C.c_uint32),
        ("szExeFile", C.c_wchar * 260),
    ]


def layout_check():
    if os.name != "nt" or C.sizeof(C.c_void_p) != 8 or C.sizeof(C.c_wchar) != 2:
        raise MetadataIncomplete(
            "architecture", "Only native Windows AMD64 x64 is supported"
        )
    expected = {
        "pbi_size": 48,
        "pbi_peb_offset": 8,
        "peb_parameters_offset": 32,
        "peb_size": 40,
        "parameters_command_offset": 112,
        "parameters_size": 128,
        "unicode_size": 16,
        "unicode_buffer_offset": 8,
        "process_entry_size": 568,
    }
    actual = {
        "pbi_size": C.sizeof(BasicInformation),
        "pbi_peb_offset": BasicInformation.PebBaseAddress.offset,
        "peb_parameters_offset": PebPrefix.ProcessParameters.offset,
        "peb_size": C.sizeof(PebPrefix),
        "parameters_command_offset": ParametersPrefix.CommandLine.offset,
        "parameters_size": C.sizeof(ParametersPrefix),
        "unicode_size": C.sizeof(UnicodeString),
        "unicode_buffer_offset": UnicodeString.Buffer.offset,
        "process_entry_size": C.sizeof(ProcessEntry),
    }
    if actual != expected or expected != EXPECTED_LAYOUT:
        raise MetadataIncomplete("layout", repr(actual))
    return actual


def validate_unicode(length, maximum, pointer):
    if (
        type(length) is not int
        or type(maximum) is not int
        or not 0 < length <= maximum
        or length % 2
        or maximum % 2
        or maximum > MAX_COMMAND_BYTES
        or not pointer
    ):
        raise MetadataIncomplete(
            "command-descriptor", "Invalid/nonempty/even bounded UNICODE_STRING"
        )


def observe_metadata(
    process_handle, job_handle, pid, birth, image, *, budget_seconds=1.0
):
    """Inspect only the supplied owned debug CREATE handle; no attach-by-PID.

    The fixed count/byte limits and monotonic checks bound work. Synchronous
    kernel APIs have no cancellation parameter; this is not hard real-time.
    """
    layout = layout_check()
    if not 0 < budget_seconds <= 2:
        raise MetadataIncomplete(
            "budget", "Only a positive at-most-two-second work budget is supported"
        )
    began = time.monotonic()
    deadline = began + budget_seconds
    k = C.WinDLL("kernel32", use_last_error=True)
    ntdll = C.WinDLL("ntdll", use_last_error=True)
    H, D = C.c_void_p, C.c_uint32
    k.GetProcessId.argtypes, k.GetProcessId.restype = [H], D
    k.GetCurrentProcess.argtypes, k.GetCurrentProcess.restype = [], H
    k.GetProcessTimes.argtypes = [H] + [C.c_void_p] * 4
    k.QueryFullProcessImageNameW.argtypes = [H, D, C.c_wchar_p, C.POINTER(D)]
    k.IsProcessInJob.argtypes = [H, H, C.POINTER(C.c_int)]
    k.IsWow64Process2.argtypes = [H, C.POINTER(C.c_uint16), C.POINTER(C.c_uint16)]
    k.ReadProcessMemory.argtypes = [H, H, H, C.c_size_t, C.POINTER(C.c_size_t)]
    k.ReadProcessMemory.restype = C.c_int
    k.CreateToolhelp32Snapshot.argtypes, k.CreateToolhelp32Snapshot.restype = [D, D], H
    k.Process32FirstW.argtypes = [H, C.POINTER(ProcessEntry)]
    k.Process32NextW.argtypes = [H, C.POINTER(ProcessEntry)]
    k.CloseHandle.argtypes = [H]
    query = ntdll.NtQueryInformationProcess
    query.argtypes, query.restype = [H, C.c_int32, H, D, C.POINTER(D)], C.c_int32

    def bound(stage):
        if time.monotonic() > deadline:
            raise MetadataIncomplete(stage, "Metadata observation budget exceeded")

    def checked(ok, stage):
        if not ok:
            raise MetadataIncomplete(stage, str(C.WinError(C.get_last_error())))

    def architecture(handle):
        a, b = C.c_uint16(), C.c_uint16()
        checked(k.IsWow64Process2(handle, C.byref(a), C.byref(b)), "IsWow64Process2")
        if a.value != 0 or b.value != 0x8664:
            raise MetadataIncomplete(
                "architecture", "WOW64/ARM/non-native x64 target is unsupported"
            )
        return {
            "process_machine": a.value,
            "native_machine": b.value,
            "pointer_bits": 64,
        }

    host_arch, target_arch = (
        architecture(k.GetCurrentProcess()),
        architecture(process_handle),
    )

    def identity():
        bound("kernel-identity")
        current_pid = int(k.GetProcessId(process_handle))
        times = [C.c_ulonglong() for _ in range(4)]
        checked(
            k.GetProcessTimes(process_handle, *[C.byref(t) for t in times]),
            "GetProcessTimes",
        )
        size, name = D(32768), C.create_unicode_buffer(32768)
        checked(
            k.QueryFullProcessImageNameW(process_handle, 0, name, C.byref(size)),
            "QueryFullProcessImageNameW",
        )
        member = C.c_int()
        checked(
            k.IsProcessInJob(process_handle, job_handle, C.byref(member)),
            "IsProcessInJob",
        )
        if (
            current_pid != pid
            or times[0].value != birth
            or not member.value
            or name.value.casefold() != str(image).casefold()
        ):
            raise MetadataIncomplete(
                "kernel-identity", "CREATE PID/birth/image/owned Job changed"
            )
        return {
            "pid": current_pid,
            "creation_filetime": times[0].value,
            "image": name.value,
            "owned_job_member": True,
        }

    before = identity()
    pbi, returned = BasicInformation(), D()
    bound("NtQueryInformationProcess")
    status = int(
        query(process_handle, 0, C.byref(pbi), C.sizeof(pbi), C.byref(returned))
    )
    if (
        status < 0
        or returned.value != C.sizeof(pbi)
        or not pbi.PebBaseAddress
        or pbi.UniqueProcessId != pid
    ):
        raise MetadataIncomplete(
            "NtQueryInformationProcess", f"NTSTATUS={status:#x}; bytes={returned.value}"
        )
    reads = []

    def memory(address, size, stage):
        bound(stage)
        if (
            not address
            or not 0 < size <= MAX_COMMAND_BYTES
            or sum(r["bytes"] for r in reads) + size > MAX_REMOTE_BYTES
        ):
            raise MetadataIncomplete(stage, "Remote-read pointer/byte limit invalid")
        buffer, actual = C.create_string_buffer(size), C.c_size_t()
        checked(
            k.ReadProcessMemory(process_handle, address, buffer, size, C.byref(actual)),
            stage,
        )
        if actual.value != size:
            raise MetadataIncomplete(stage, "Short ReadProcessMemory")
        reads.append({"stage": stage, "bytes": size})
        return buffer.raw

    peb_raw = memory(pbi.PebBaseAddress, C.sizeof(PebPrefix), "PEB")
    peb = PebPrefix.from_buffer_copy(peb_raw)
    parameters_raw = memory(
        peb.ProcessParameters, C.sizeof(ParametersPrefix), "ProcessParameters"
    )
    parameters = ParametersPrefix.from_buffer_copy(parameters_raw)
    command = parameters.CommandLine
    validate_unicode(command.Length, command.MaximumLength, command.Buffer)
    command_raw = memory(command.Buffer, command.Length, "CommandLine")
    if (
        memory(
            peb.ProcessParameters,
            C.sizeof(ParametersPrefix),
            "ProcessParameters-recheck",
        )
        != parameters_raw
    ):
        raise MetadataIncomplete(
            "command-stability", "ProcessParameters changed during observation"
        )
    if memory(command.Buffer, command.Length, "CommandLine-recheck") != command_raw:
        raise MetadataIncomplete(
            "command-stability", "CommandLine changed during observation"
        )
    if memory(pbi.PebBaseAddress, C.sizeof(PebPrefix), "PEB-recheck") != peb_raw:
        raise MetadataIncomplete(
            "command-stability", "PEB parameters pointer changed during observation"
        )
    try:
        text = command_raw.decode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        raise MetadataIncomplete("command-encoding", str(exc)) from exc
    if not text or "\0" in text:
        raise MetadataIncomplete(
            "command-encoding", "Empty or embedded-NUL command observation"
        )
    snapshot = k.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, C.c_void_p(-1).value):
        raise MetadataIncomplete("Toolhelp", str(C.WinError(C.get_last_error())))
    parent, seen = None, 0
    try:
        entry = ProcessEntry()
        entry.dwSize = C.sizeof(entry)
        checked(k.Process32FirstW(snapshot, C.byref(entry)), "Process32FirstW")
        while True:
            bound("Toolhelp-parent")
            seen += 1
            if seen > MAX_SNAPSHOT_PROCESSES:
                raise MetadataIncomplete(
                    "Toolhelp-parent", "Snapshot process-count limit exceeded"
                )
            if entry.th32ProcessID == pid:
                parent = int(entry.th32ParentProcessID)
                if entry.szExeFile.casefold() != Path(image).name.casefold():
                    raise MetadataIncomplete(
                        "Toolhelp-parent", "Snapshot image disagrees with CREATE handle"
                    )
                break
            if not k.Process32NextW(snapshot, C.byref(entry)):
                raise MetadataIncomplete(
                    "Toolhelp-parent", "CREATE PID missing from snapshot"
                )
    finally:
        checked(k.CloseHandle(snapshot), "CloseHandle-snapshot")
    if parent != int(pbi.InheritedFromUniqueProcessId) or not parent:
        raise MetadataIncomplete("parent", "PBI and Toolhelp parent PID disagree")
    after = identity()
    if before != after:
        raise MetadataIncomplete("kernel-identity", "CREATE kernel identity changed")
    bound("complete")
    return {
        "provider": PROVIDER,
        "ProcessId": pid,
        "ParentProcessId": parent,
        "CommandLine": text,
        "creation_filetime": birth,
        "birth_precision_ticks": 1,
        "kernel_identity_before": before,
        "kernel_identity_after": after,
        "host_architecture": host_arch,
        "target_architecture": target_arch,
        "pbi_parent_pid": int(pbi.InheritedFromUniqueProcessId),
        "toolhelp_parent_pid": parent,
        "command_line_utf16_sha256": hashlib.sha256(command_raw).hexdigest(),
        "command_line_bytes": len(command_raw),
        "remote_reads": reads,
        "layout": layout,
        "elapsed_ms": (time.monotonic() - began) * 1000,
        "budget_seconds": budget_seconds,
        "identity_policy": "Same owned CREATE handle kernel birth/image/PID checked twice; not an independent CIM birth observation",
        "command_line_policy": "Stable bounded PEB user-memory observation while CREATE debug event held; mutable by target, not kernel truth",
    }
