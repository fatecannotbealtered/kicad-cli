"""Harmless standalone fixture. Writes ONLY paths supplied under its own case."""

import argparse
import ctypes as C
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path


def wait_for(path, timeout=5):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Own fixture coordination file was not created")
        time.sleep(0.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("directory", type=Path)
    parser.add_argument("operation", nargs="?", default="")
    args = parser.parse_args()
    root, mode, operation = args.directory.resolve(), args.mode, args.operation
    if mode == "sentinel":
        (root / "ready").write_text(str(os.getpid()), encoding="ascii")
        wait_for(root / "release", timeout=300)
        return
    if mode == "child_wait":
        (root / "child-ready").write_text(str(os.getpid()), encoding="ascii")
        # Remain a live owned child for the whole parent output boundary.
        # This is fixture coordination, never the controller's trigger.
        wait_for(root / "child-release", timeout=60)
        return
    if mode == "timeout":
        wait_for(root / "never-created", timeout=60)
        return
    if mode in ("safety_failfast", "safety_second_chance"):
        kernel = C.WinDLL("kernel32", use_last_error=True)
        if mode == "safety_failfast":
            kernel.RaiseException.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32, C.c_void_p]
            kernel.RaiseException.restype = None
            kernel.RaiseException(0xC0000409, 0, 0, None)
        else:
            # ctypes catches SEH across its own call boundary. A new native
            # thread running the system DebugBreak API has no ctypes handler.
            # No injected bytes or changed image. x64 tolerates the unused
            # LPVOID argument; the guard terminates before this API can return.
            kernel.DebugBreak.argtypes, kernel.DebugBreak.restype = [], None
            kernel.CreateThread.argtypes = [
                C.c_void_p,
                C.c_size_t,
                C.c_void_p,
                C.c_void_p,
                C.c_uint32,
                C.POINTER(C.c_uint32),
            ]
            kernel.CreateThread.restype = C.c_void_p
            kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
            kernel.WaitForSingleObject.restype = C.c_uint32
            tid = C.c_uint32()
            thread = kernel.CreateThread(
                None, 0, C.cast(kernel.DebugBreak, C.c_void_p), None, 0, C.byref(tid)
            )
            if not thread:
                raise C.WinError(C.get_last_error())
            kernel.WaitForSingleObject(thread, 0xFFFFFFFF)
        raise RuntimeError("Safety exception unexpectedly returned to fixture")
    if mode == "handled_exception":
        kernel = C.WinDLL("kernel32", use_last_error=True)
        callback_type = C.WINFUNCTYPE(C.c_long, C.c_void_p)
        invoked = []

        @callback_type
        def handler(pointers):
            record = C.c_void_p.from_address(pointers).value
            code = C.c_uint32.from_address(record).value
            if code == 0xE0424444:
                invoked.append(code)
                return -1  # EXCEPTION_CONTINUE_EXECUTION, application handler
            return 0

        kernel.AddVectoredExceptionHandler.argtypes = [C.c_uint32, callback_type]
        kernel.AddVectoredExceptionHandler.restype = C.c_void_p
        kernel.RemoveVectoredExceptionHandler.argtypes = [C.c_void_p]
        kernel.RemoveVectoredExceptionHandler.restype = C.c_uint32
        kernel.RaiseException.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32, C.c_void_p]
        kernel.RaiseException.restype = None
        handle = kernel.AddVectoredExceptionHandler(1, handler)
        if not handle:
            raise C.WinError(C.get_last_error())
        try:
            kernel.RaiseException(0xE0424444, 0, 0, None)
        finally:
            kernel.RemoveVectoredExceptionHandler(handle)
        (root / "application-handler.json").write_text(
            json.dumps({"handler_invocations": len(invoked)}), encoding="utf-8"
        )
    if mode == "child_live":
        child_cache = root / "child-empty-pycache"
        child_cache.mkdir()
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-S",
                "-X",
                "pycache_prefix=" + str(child_cache),
                __file__,
                "child_wait",
                str(root),
            ],
            creationflags=0x08000000,
        )
        wait_for(root / "child-ready")
    if mode == "helper_tree":
        completed = subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/c", "ver"],
            check=True,
            capture_output=True,
            creationflags=0x08000000,
        )
        (root / "helper-ver.bin").write_bytes(completed.stdout)

    if mode != "missing_save":
        (root / "own-project.json").write_text(
            json.dumps(
                {"fixture": "owned harmless project", "operation_id": operation, "saved": True},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    database = sqlite3.connect(root / "receipt.sqlite3")
    database.execute("CREATE TABLE receipts (operation_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    database.commit()
    if mode != "missing_receipt":
        receipt_id = "different-operation" if mode == "wrong_receipt_operation" else operation
        database.execute("INSERT INTO receipts VALUES (?, 'succeeded')", (receipt_id,))
        if mode != "uncommitted_receipt":
            database.commit()

    if mode == "no_stdout":
        database.close()
        return
    if mode == "nonmatching_stdout":
        print(json.dumps({"ok": False, "schema_version": "1.0"}), flush=True)
        database.close()
        return
    if mode == "prior_stdout":
        os.write(1, b"prior\n")

    def emit():
        actual_id = "different-operation" if mode == "wrong_stdout_operation" else operation
        # Deliberately private synthetic value verifies candidate buffers never
        # reach public reports or simulated caller unless the boundary is refused.
        value = json.dumps(
            {
                "ok": True,
                "schema_version": "1.0",
                "data": {
                    "operation_id": actual_id,
                    "confirm_token": "synthetic-private-fixture-value",
                },
            }
        )
        if mode in ("explicit_kernelbase", "reused_stdout_file"):
            kernel = C.WinDLL("kernel32", use_last_error=True)
            base = C.WinDLL(
                str(Path(os.environ["SystemRoot"]) / "System32/KernelBase.dll"), use_last_error=True
            )
            kernel.GetStdHandle.argtypes, kernel.GetStdHandle.restype = [C.c_int32], C.c_void_p
            base.WriteFile.argtypes = [
                C.c_void_p,
                C.c_void_p,
                C.c_uint32,
                C.POINTER(C.c_uint32),
                C.c_void_p,
            ]
            base.WriteFile.restype = C.c_int
            destination = kernel.GetStdHandle(-11)
            if mode == "reused_stdout_file":
                kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [C.c_void_p], C.c_int
                kernel.CreateFileW.argtypes = [
                    C.c_wchar_p,
                    C.c_uint32,
                    C.c_uint32,
                    C.c_void_p,
                    C.c_uint32,
                    C.c_uint32,
                    C.c_void_p,
                ]
                kernel.CreateFileW.restype = C.c_void_p
                old = destination
                if not kernel.CloseHandle(old):
                    raise C.WinError(C.get_last_error())
                destination = kernel.CreateFileW(
                    str(root / "misdirected-stdout.json"), 0x40000000, 1, None, 1, 0x80, None
                )
                if destination == C.c_void_p(-1).value:
                    raise C.WinError(C.get_last_error())
                (root / "reused-handle.json").write_text(
                    json.dumps({"numeric_stdout_handle_reused": destination == old}),
                    encoding="utf-8",
                )
            raw, count = (value + "\n").encode(), C.c_uint32()
            buffer = C.create_string_buffer(raw)
            if not base.WriteFile(destination, buffer, len(raw), C.byref(count), None):
                raise C.WinError(C.get_last_error())
            if mode == "reused_stdout_file":
                kernel.CloseHandle(destination)
        else:
            print(value, flush=True)

    if mode == "multithread":
        ready, release = threading.Event(), threading.Event()

        def file_writer():
            with (root / "other-file.bin").open("wb", buffering=0) as stream:
                for index in range(40):
                    stream.write(("unrelated file write " + str(index) + "\n").encode())
            ready.set()
            release.wait()

        workers = [threading.Thread(target=file_writer) for _ in range(3)]
        for worker in workers:
            worker.start()
        ready.wait()
        stdout_worker = threading.Thread(target=emit)
        stdout_worker.start()
        stdout_worker.join()
        release.set()
        for worker in workers:
            worker.join()
    else:
        emit()
    database.close()
    if mode == "child_live":
        (root / "child-release").write_text("natural release", encoding="ascii")


if __name__ == "__main__":
    main()
