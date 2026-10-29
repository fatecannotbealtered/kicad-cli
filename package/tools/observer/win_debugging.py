"""Observe only a newly-created owned Windows x64 debuggee.

Uses installed kernel32/dbghelp APIs. No attach-by-PID entry point, registry,
privilege adjustment, symbol server, installation or vendor-file modification.
Raw dumps and register/stack data must remain local.
"""

import ctypes as C
import json
import os
import struct
import time
from pathlib import Path

from process_evidence import sha, write

D, Q, H, W = C.c_uint32, C.c_uint64, C.c_void_p, C.c_uint16


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
        ("unicode", W),
    ]


class LoadInfo(C.Structure):
    _fields_ = [
        ("file", H),
        ("base", H),
        ("debug_offset", D),
        ("debug_size", D),
        ("name", H),
        ("unicode", W),
    ]


class EventData(C.Union):
    _fields_ = [
        ("exception", ExceptionInfo),
        ("create", CreateInfo),
        ("load", LoadInfo),
        ("exit_code", D),
        ("storage", C.c_byte * 160),
    ]


class Event(C.Structure):
    _fields_ = [("code", D), ("pid", D), ("tid", D), ("data", EventData)]


class Address(C.Structure):
    _fields_ = [("offset", Q), ("segment", W), ("mode", D)]


class Frame(C.Structure):
    # Prefix through Reserved follows STACKFRAME64. Extra zero storage provides
    # the documented KDHELP64 tail without reading version-specific tail fields.
    _fields_ = [
        ("pc", Address),
        ("ret", Address),
        ("frame", Address),
        ("stack", Address),
        ("bstore", Address),
        ("function_table", H),
        ("params", Q * 4),
        ("far", D),
        ("virtual", D),
        ("reserved", Q * 3),
        ("kdhelp", C.c_byte * 512),
    ]


class Symbol(C.Structure):
    _fields_ = [
        ("size", D),
        ("type_index", D),
        ("reserved", Q * 2),
        ("index", D),
        ("symbol_size", D),
        ("module_base", Q),
        ("flags", D),
        ("value", Q),
        ("address", Q),
        ("register", D),
        ("scope", D),
        ("tag", D),
        ("name_length", D),
        ("max_name_length", D),
        ("name", C.c_char * 1),
    ]


def file_version(path):
    dll = C.WinDLL("version", use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes = [C.c_wchar_p, C.c_void_p]
    dll.GetFileVersionInfoW.argtypes = [C.c_wchar_p, D, D, H]
    dll.VerQueryValueW.argtypes = [H, C.c_wchar_p, C.POINTER(H), C.POINTER(D)]
    size = dll.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buffer = C.create_string_buffer(size)
    if not dll.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    pointer, count = H(), D()
    if not dll.VerQueryValueW(buffer, "\\", C.byref(pointer), C.byref(count)):
        return None
    values = C.cast(pointer, C.POINTER(D))
    return ".".join(
        str(v) for v in (values[2] >> 16, values[2] & 65535, values[3] >> 16, values[3] & 65535)
    )


class Debugger:
    def __init__(self, process, directory):
        if os.name != "nt" or C.sizeof(H) != 8:
            raise ValueError("Installed Windows x64 diagnostics only")
        assert C.sizeof(ExceptionRecord) == 152 and C.sizeof(Event) == 176
        self.process, self.directory = process, Path(directory)
        self.directory.mkdir(exist_ok=False)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        # Force the system DLL; no application/search-path DLL substitution.
        self.dbg = C.WinDLL(
            str(Path(os.environ["SystemRoot"]) / "System32/dbghelp.dll"), use_last_error=True
        )
        k, d = self.kernel, self.dbg
        k.WaitForDebugEventEx.argtypes = [C.POINTER(Event), D]
        k.ContinueDebugEvent.argtypes = [D, D, D]
        k.GetFinalPathNameByHandleW.argtypes = [H, C.c_wchar_p, D, D]
        k.GetFinalPathNameByHandleW.restype = D
        k.CloseHandle.argtypes = [H]
        k.OpenThread.argtypes = [D, D, D]
        k.OpenThread.restype = H
        k.GetThreadContext.argtypes = [H, H]
        d.SymSetOptions.argtypes = [D]
        d.SymInitialize.argtypes = [H, C.c_char_p, D]
        d.SymCleanup.argtypes = [H]
        d.SymFromAddr.argtypes = [H, Q, C.POINTER(Q), H]
        d.SymFunctionTableAccess64.argtypes = [H, Q]
        d.SymFunctionTableAccess64.restype = H
        d.SymGetModuleBase64.argtypes = [H, Q]
        d.SymGetModuleBase64.restype = Q
        d.StackWalk64.argtypes = [D, H, H, H, H, H, H, H, H]
        d.MiniDumpWriteDump.argtypes = [H, D, H, D, H, H, H]
        self.modules, self.exceptions = [], []
        self.initial_breakpoint_seen = False
        self.hprocess = H(int(process._handle))

    def module(self, handle, base):
        entry = {"base": int(base or 0), "path": None, "image_size": None}
        if handle:
            buffer = C.create_unicode_buffer(32768)
            size = self.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            if 0 < size < len(buffer):
                entry["path"] = buffer.value.removeprefix("\\\\?\\")
                try:
                    with Path(entry["path"]).open("rb") as stream:
                        stream.seek(0x3C)
                        offset = struct.unpack("<I", stream.read(4))[0]
                        stream.seek(offset + 24 + 56)
                        entry["image_size"] = struct.unpack("<I", stream.read(4))[0]
                except (OSError, ValueError, struct.error):
                    pass
            self.kernel.CloseHandle(handle)
        self.modules.append(entry)

    def address(self, address):
        item = {"address": hex(address)}
        module = next(
            (m for m in self.modules if m["base"] <= address < m["base"] + (m["image_size"] or 0)),
            None,
        )
        if module:
            item.update(module=module["path"], offset=hex(address - module["base"]))
        return item

    def context_and_dump(self, event):
        thread = self.kernel.OpenThread(0x48, False, event.tid)
        if not thread:
            raise C.WinError(C.get_last_error())
        buffer = C.create_string_buffer(1232 + 16)
        address = (C.addressof(buffer) + 15) & ~15
        C.c_uint32.from_address(address + 48).value = 0x10001F
        try:
            if not self.kernel.GetThreadContext(thread, address):
                raise C.WinError(C.get_last_error())
            registers = {
                name: C.c_uint64.from_address(address + offset).value
                for name, offset in (
                    ("rax", 120),
                    ("rcx", 128),
                    ("rdx", 136),
                    ("rbx", 144),
                    ("rsp", 152),
                    ("rbp", 160),
                    ("rsi", 168),
                    ("rdi", 176),
                    ("rip", 248),
                )
            }
            unwind_buffer = C.create_string_buffer(1232 + 16)
            unwind_address = (C.addressof(unwind_buffer) + 15) & ~15
            C.memmove(unwind_address, address, 1232)
            frame = Frame()
            frame.pc.offset, frame.frame.offset, frame.stack.offset = (
                registers["rip"],
                registers["rbp"],
                registers["rsp"],
            )
            frame.pc.mode = frame.frame.mode = frame.stack.mode = 3
            symbols = self.directory / "local-symbols"
            symbols.mkdir(exist_ok=True)
            # Ignore ambient symbol paths, disable server autodetection/prompts
            # and CodeView path lookups. Only local images/exports are available.
            self.dbg.SymSetOptions(0x2 | 0x4 | 0x200 | 0x1000 | 0x80000 | 0x02000000 | 0x80)
            initialized = bool(
                self.dbg.SymInitialize(self.hprocess, str(symbols).encode("utf-8"), True)
            )
            stack = []
            try:
                for _ in range(64):
                    row = self.address(frame.pc.offset)
                    if initialized:
                        symbol_buffer = C.create_string_buffer(C.sizeof(Symbol) + 1024)
                        symbol = Symbol.from_buffer(symbol_buffer)
                        symbol.size, symbol.max_name_length = C.sizeof(Symbol), 1024
                        displacement = Q()
                        if self.dbg.SymFromAddr(
                            self.hprocess, frame.pc.offset, C.byref(displacement), symbol_buffer
                        ):
                            row["symbol"] = C.string_at(
                                C.addressof(symbol_buffer) + Symbol.name.offset,
                                min(symbol.name_length, 1024),
                            ).decode("utf-8", "replace")
                            row["symbol_displacement"] = int(displacement.value)
                    stack.append(row)
                    if not initialized or not self.dbg.StackWalk64(
                        0x8664,
                        self.hprocess,
                        thread,
                        C.byref(frame),
                        unwind_address,
                        None,
                        C.cast(self.dbg.SymFunctionTableAccess64, H),
                        C.cast(self.dbg.SymGetModuleBase64, H),
                        None,
                    ):
                        break
                    if not frame.pc.offset or (
                        len(stack) > 1 and row["address"] == hex(frame.pc.offset)
                    ):
                        break
            finally:
                if initialized:
                    self.dbg.SymCleanup(self.hprocess)

            class Pointers(C.Structure):
                _fields_ = [("record", H), ("context", H)]

            class DumpInfo(C.Structure):
                _pack_ = 4  # minidumpapiset serialization ABI
                _fields_ = [("thread_id", D), ("pointers", H), ("client_pointers", D)]

            pointers = Pointers(C.addressof(event.data.exception.record), address)
            info = DumpInfo(event.tid, C.addressof(pointers), False)
            dump = self.directory / ("exception-" + str(len(self.exceptions)) + ".dmp")
            import msvcrt

            with dump.open("xb") as stream:
                ok = self.dbg.MiniDumpWriteDump(
                    self.hprocess,
                    self.process.pid,
                    H(msvcrt.get_osfhandle(stream.fileno())),
                    0x2 | 0x4 | 0x1000 | 0x800,  # full memory, handles and thread information
                    C.byref(info),
                    None,
                    None,
                )
                error = C.get_last_error()
            return {
                "registers": {k: hex(v) for k, v in registers.items()},
                "stack": stack,
                "modules": self.modules,
                "symbol_policy": "local images/exports only; no network or symbol-server setup",
                "dump": {
                    "path": str(dump),
                    "succeeded": bool(ok),
                    "error": error if not ok else None,
                    "sha256": sha(dump),
                    "bytes": dump.stat().st_size,
                    "local_only": True,
                },
                "context_scope": "Windows AMD64, 16-byte aligned CONTEXT at debugger event",
            }
        finally:
            self.kernel.CloseHandle(thread)

    def monitor(self, job, timeout):
        # Linear append-only capture while the debuggee is stopped. Rewriting
        # the entire exception list at every first-chance event can itself make
        # a large board time out. Never reuse a previous journal.
        with (self.directory / "exceptions.jsonl").open("x", encoding="utf-8") as journal:
            try:
                return self._monitor(job, timeout, journal)
            finally:
                write(self.directory / "exceptions.json", self.exceptions)

    def _monitor(self, job, timeout, journal):
        def append(kind, index, row):
            journal.write(json.dumps({"kind": kind, "event_index": index, "event": row}) + "\n")
            journal.flush()

        deadline, timed_out, execution = time.monotonic() + timeout, False, None
        while True:
            event = Event()
            if time.monotonic() > deadline:
                if timed_out:
                    raise TimeoutError("Owned debuggee failed to exit after termination")
                job.terminate()
                timed_out, deadline = True, time.monotonic() + 5
            if not self.kernel.WaitForDebugEventEx(C.byref(event), 100):
                error = C.get_last_error()
                if error not in (0, 121):  # ERROR_SEM_TIMEOUT
                    raise C.WinError(error)
                continue
            if event.pid != self.process.pid:
                raise RuntimeError("DEBUG_ONLY_THIS_PROCESS reported an unexpected target")
            continuation = 0x00010002
            if event.code == 3:
                self.module(event.data.create.file, event.data.create.base)
            elif event.code == 6:
                self.module(event.data.load.file, event.data.load.base)
            elif event.code == 1:
                info = event.data.exception
                row = {
                    "code": hex(info.record.code),
                    "first_chance": bool(info.first_chance),
                    "thread_id": event.tid,
                    "fault": self.address(int(info.record.address or 0)),
                    "flags": int(info.record.flags),
                    "parameters": [
                        int(info.record.information[i]) for i in range(min(info.record.count, 15))
                    ],
                }
                initial = (
                    info.record.code == 0x80000003
                    and info.first_chance
                    and not self.initial_breakpoint_seen
                )
                if initial:
                    self.initial_breakpoint_seen = True
                    row["bootstrap_breakpoint"] = True
                else:
                    continuation = 0x80010001  # preserve normal exception handling
                index = len(self.exceptions)
                # Keep the exception identity even if context/dump collection
                # fails or is interrupted. Capture updates reference this index.
                append("exception", index, row)
                if not info.first_chance or info.record.code in (0xC0000409, 0xC0000602):
                    try:
                        row["scene"] = self.context_and_dump(event)
                    except (OSError, ValueError, RuntimeError) as exc:
                        row["capture_refused_or_failed"] = str(exc)
                    append("capture", index, row)
                self.exceptions.append(row)
            elif event.code == 5:
                code = event.data.exit_code
                execution = (
                    "timeout"
                    if timed_out
                    else "completed"
                    if code == 0
                    else ("crashed" if code >= 0xC0000000 else "nonzero_exit")
                )
            if not self.kernel.ContinueDebugEvent(event.pid, event.tid, continuation):
                raise C.WinError(C.get_last_error())
            if execution:
                break
        self.process.wait(timeout=5)
        for module in self.modules:
            if module["path"]:
                try:
                    module.update(
                        sha256=sha(module["path"]), file_version=file_version(module["path"])
                    )
                except OSError as exc:
                    module["read_error"] = str(exc)
        result = {
            "execution": execution,
            "exit": self.process.returncode,
            "pid": self.process.pid,
            "exceptions": self.exceptions,
            "exception_journal": {
                "sha256": sha(self.directory / "exceptions.jsonl"),
                "format": "JSONL exception identities; optional capture updates by event_index",
                "interrupted_prefix_is_not_complete_observation": True,
            },
            "modules": self.modules,
            "dbghelp": {
                "path": str(Path(os.environ["SystemRoot"]) / "System32/dbghelp.dll"),
                "version": file_version(Path(os.environ["SystemRoot"]) / "System32/dbghelp.dll"),
            },
            "safety": "Owned new child only; no attach/global debugger/registry/privilege changes",
            "historical_root_cause": "not inferred from exception code or module offset",
        }
        write(self.directory / "result.json", result)
        return result
